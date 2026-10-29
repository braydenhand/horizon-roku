"""Companion service for the Home launcher. Runs on another machine on the LAN.

1. Launch relay: the TV refuses ECP requests from itself ("ECP is not allowed
   from loopback address") and roAppManager.LaunchApp can't switch inputs, so
   the launcher asks this service to do it: POST http://<watcher>:8099/launch/<id>
2. Redirect (--redirect): listens to the TV's ECP-2 events and opens the
   launcher when the TV lands on Roku's home screen after powering on, leaving
   an app, or leaving an HDMI input / Live TV (polled, since the TV sends no
   event for that). Leaving the launcher itself (Home pressed in it) stays on
   Roku home.
3. Photos and glass for the launcher and screensaver: GET /wallpaper (a photo
   for the time of day), /photos[?auto=1], /photo/<name>[?size=home|saver], /blur/<name>,
   /glass/<name>?x=&y=&w=&h=&r=[&fade=l|r] and /weather. See media.py.
   GET /wallpaper?pin=<name> returns that photo instead; /gallery and
   /thumb/<name> feed the wallpaper picker.
4. Installed apps for the All apps sheet: GET /apps and /icon/<id>.

    python watcher.py [--redirect] [--listen ADDRESS] [--port 8099] [-v]

Reads config.json next to it: {"tv": ..., "listen": ..., "port": ..., "weather":
{"latitude": ..., "longitude": ..., "unit": "fahrenheit"}}. deploy_pi.py writes
it from the main config.json.
"""
import argparse
import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from media import Photos, Wallpapers, Weather, rounded_icon
from roku_ecp import EcpSession

ROOT = Path(__file__).resolve().parent
LAUNCHER_ID = "dev"
EVENTS = ["power-mode-changed", "plugin-ui-run", "plugin-ui-exit"]
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

log = logging.getLogger("watcher")


def ecp_launch(tv, app_id, params=""):
    """Plain HTTP ECP launch; allowed in Limited mode when sent from another device."""
    req = urllib.request.Request(f"http://{tv}:8060/launch/{app_id}{params}", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


def ecp_power(tv):
    """(powered_on, uptime_seconds) from device-info, which Limited mode still serves."""
    with urllib.request.urlopen(f"http://{tv}:8060/query/device-info", timeout=5) as resp:
        root = ET.fromstring(resp.read())
    return root.findtext("power-mode") == "PowerOn", int(root.findtext("uptime") or 0)


# ---------------------------------------------------------------- watcher

def is_input(app):
    return app["type"] == "tvin" or (app["id"] or "").startswith("tvinput.")


class Watcher:
    def __init__(self, tv, redirect, photos=None, weather=None):
        self.tv = tv
        self.redirect = redirect
        self.photos = photos
        self.weather = weather
        self.wallpapers = Wallpapers(photos, weather) if photos else None
        self.icon_cache = None
        if photos:
            self.icon_cache = photos.cache.parent / "icons"
            self.icon_cache.mkdir(parents=True, exist_ok=True)
        self._apps = None
        self._apps_at = 0.0
        self._icon_lock = asyncio.Lock()
        self.pending = None      # delayed "are we on Roku home?" check
        self.input_watch = None  # polling loop while an HDMI input / Live TV is showing
        self.powered_on = None   # last known TV power state (None = unknown)

    # ---- HTTP: launch relay, photos, glass, weather

    async def route(self, method, path, query):
        """Return (status, content type, body bytes)."""
        if method == "POST" and path.startswith("/launch/"):
            app_id = path[len("/launch/"):]
            if not SAFE_ID.match(app_id):
                return 400, "text/plain", b"bad app id"
            status = await asyncio.to_thread(ecp_launch, self.tv, app_id)
            log.info("relay: launched %s -> %s", app_id, status)
            if app_id.startswith("tvinput."):
                self.start_input_watch()
            return 200, "text/plain", b"ok"
        if method != "GET":
            return 404, "text/plain", b"not found"
        if path == "/health":
            return 200, "text/plain", b"ok"
        if path == "/photos" and self.photos:
            # ?auto=1: only photos that suit the time of day
            names = await asyncio.to_thread(
                self.wallpapers.pool if query.get("auto") == "1" else self.photos.names)
            body = {"photos": ["/photo/" + urllib.parse.quote(n) for n in names]}
            return 200, "application/json", json.dumps(body).encode()
        if path.startswith("/photo/") and self.photos:
            name = urllib.parse.unquote(path[len("/photo/"):])
            data = await asyncio.to_thread(self.photos.jpeg, name, query.get("size", "home"))
            return (200, "image/jpeg", data) if data else (404, "text/plain", b"no such photo")
        if path.startswith("/thumb/") and self.photos:
            data = await asyncio.to_thread(self.photos.thumb, urllib.parse.unquote(path[len("/thumb/"):]))
            return (200, "image/png", data) if data else (404, "text/plain", b"no such photo")
        if path == "/gallery" and self.photos:
            names = await asyncio.to_thread(self.photos.names)
            body = {"photos": [{"name": n, "thumb": "/thumb/" + urllib.parse.quote(n), **self.photos.info(n)}
                               for n in names]}
            return 200, "application/json", json.dumps(body).encode()
        if path.startswith("/blur/") and self.photos:
            data = await asyncio.to_thread(self.photos.blurred, urllib.parse.unquote(path[len("/blur/"):]))
            return (200, "image/jpeg", data) if data else (404, "text/plain", b"no such photo")
        if path.startswith("/glass/") and self.photos:
            name = urllib.parse.unquote(path[len("/glass/"):])
            try:
                x, y, w, h, r = (int(query[k]) for k in ("x", "y", "w", "h", "r"))
                fade_width = int(query.get("fw", 132))
            except (KeyError, ValueError):
                return 400, "text/plain", b"glass needs integer x, y, w, h, r"
            data = await asyncio.to_thread(self.photos.glass, name, x, y, w, h, r,
                                           query.get("fade"), fade_width)
            return (200, "image/png", data) if data else (404, "text/plain", b"no such photo")
        if path == "/apps":
            apps = await self.installed_apps()
            asyncio.create_task(self.prefetch_icons(apps))  # usually cached before the grid asks
            body = {"apps": [{"id": a["id"], "name": a["name"], "icon": "/icon/" + a["id"]} for a in apps]}
            return 200, "application/json", json.dumps(body).encode()
        if path.startswith("/icon/") and self.icon_cache:
            app_id = path[len("/icon/"):]
            if not SAFE_ID.match(app_id):
                return 400, "text/plain", b"bad app id"
            return 200, "image/png", await self.app_icon(app_id)
        if path == "/wallpaper" and self.wallpapers:
            choice = await asyncio.to_thread(self.wallpapers.choose, None, query.get("pin"))
            if not choice:
                return 404, "text/plain", b"no photos"
            log.info("wallpaper: %s (%s)", choice["name"], choice["period"])
            return 200, "application/json", json.dumps(choice).encode()
        if path == "/weather" and self.weather:
            data = await asyncio.to_thread(self.weather.current)
            return (200, "application/json", json.dumps(data).encode()) if data else (404, "text/plain", b"no weather")
        return 404, "text/plain", b"not found"

    async def installed_apps(self):
        """Installed apps (not inputs, not this launcher), by name; cached 10 minutes.
        The launcher can't ask the TV itself: ECP refuses requests from the TV."""
        now = asyncio.get_running_loop().time()
        if self._apps is None or now - self._apps_at > 600:
            async with EcpSession(self.tv) as session:
                apps = await session.apps()
            self._apps = sorted((a for a in apps if a["type"] == "appl" and a["id"] != LAUNCHER_ID),
                                key=lambda a: a["name"].casefold())
            self._apps_at = now
        return self._apps

    async def app_icon(self, app_id):
        """Rounded icon PNG for an app, refreshed weekly (apps update their artwork).
        Fetched one at a time: the TV refuses (503) several WebSocket sessions at once."""
        path = self.icon_cache / f"{app_id}.png"
        if path.exists() and time.time() - path.stat().st_mtime < 7 * 24 * 3600:
            return path.read_bytes()
        async with self._icon_lock:
            if not path.exists() or time.time() - path.stat().st_mtime >= 7 * 24 * 3600:
                async with EcpSession(self.tv) as session:
                    artwork = await session.icon(app_id)
                path.write_bytes(await asyncio.to_thread(rounded_icon, artwork))
        return path.read_bytes()

    async def prefetch_icons(self, apps):
        for app in apps:
            try:
                await self.app_icon(app["id"])
            except Exception as e:
                log.warning("icon for %s failed (%s: %s)", app["id"], type(e).__name__, e)

    async def handle_http(self, reader, writer):
        try:
            request_line = (await asyncio.wait_for(reader.readline(), 5)).decode("latin-1")
            while (await asyncio.wait_for(reader.readline(), 5)) not in (b"\r\n", b"\n", b""):
                pass  # headers are not needed
            parts = request_line.split()
            if len(parts) < 2:
                status, ctype, body = 400, "text/plain", b"bad request"
            else:
                log.debug("http %s %s", parts[0], parts[1])
                url = urllib.parse.urlsplit(parts[1])
                query = dict(urllib.parse.parse_qsl(url.query))
                status, ctype, body = await self.route(parts[0], url.path, query)
        except Exception as e:
            status, ctype, body = 502, "text/plain", f"{type(e).__name__}: {e}".encode()
            log.warning("http error: %s", body.decode())
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 502: "Bad Gateway"}.get(status, "OK")
        try:
            writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\n"
                         f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            await writer.drain()
        finally:
            writer.close()

    # ---- redirect

    async def open_launcher(self, reason, attempts=4):
        """Launch Horizon. Right after waking, the TV can answer 503 for a moment."""
        for attempt in range(attempts):
            try:
                status = await asyncio.to_thread(ecp_launch, self.tv, LAUNCHER_ID, f"?reason={reason}")
                log.info("redirect (%s): opened launcher -> %s", reason, status)
                return
            except OSError as e:  # HTTPError and URLError are OSErrors
                log.info("redirect (%s): launch failed (%s), attempt %d", reason, e, attempt + 1)
                await asyncio.sleep(1.0)

    async def check_and_redirect(self, reason, delay, checks=8, interval=0.35):
        """Open the launcher once the TV settles on Roku's home screen. The exiting
        app can still be reported as active briefly, so re-check a few times."""
        await asyncio.sleep(delay)
        async with EcpSession(self.tv) as session:
            for _ in range(checks):
                app = await session.active_app()
                log.debug("redirect (%s): screen shows %s (%s)", reason, app["id"], app["type"])
                # Right after leaving an app the TV briefly reports no app at all on
                # its way to Roku home. Acting on that saves about a second.
                if app["type"] == "home" or (reason == "appexit" and not app["id"]):
                    await self.open_launcher(reason)
                    return
                if is_input(app):
                    log.info("redirect (%s): on %s, leaving it", reason, app["id"])
                    self.start_input_watch()
                    return
                await asyncio.sleep(interval)
        log.info("redirect (%s): on %s, leaving it", reason, app["id"])

    def schedule_check(self, reason, delay):
        self.cancel(self.pending)
        self.pending = asyncio.create_task(self.check_and_redirect(reason, delay))

    def start_input_watch(self):
        """Inputs switch to Roku home without any ECP event, so poll while on one."""
        if not self.redirect or (self.input_watch and not self.input_watch.done()):
            return
        self.input_watch = asyncio.create_task(self.poll_input())

    async def poll_input(self, interval=0.75, arm_timeout=10.0, confirm=2):
        """While switching, the TV briefly reports Roku home or no app at all, so
        only act after the input has actually been seen, and only on a home
        screen seen `confirm` times in a row."""
        log.info("input watch: started")
        loop = asyncio.get_running_loop()
        give_up_at = loop.time() + arm_timeout
        armed = False
        home_streak = 0
        try:
            async with EcpSession(self.tv) as session:
                while True:
                    await asyncio.sleep(interval)
                    app = await session.active_app()
                    if is_input(app):
                        if not armed:
                            log.info("input watch: on %s", app["id"])
                        armed, home_streak = True, 0
                        continue
                    if not armed:
                        if loop.time() > give_up_at:
                            log.info("input watch: input never appeared (now %s)", app["id"])
                            return
                        continue
                    if app["type"] == "home":
                        home_streak += 1
                        if home_streak >= confirm:
                            await self.open_launcher("inputexit")
                            return
                        continue
                    if not app["id"]:
                        continue  # transitional blank reading
                    log.info("input watch: left input for %s", app["id"])
                    return
        except asyncio.CancelledError:
            log.info("input watch: stopped")
            raise
        except Exception as e:
            log.warning("input watch failed (%s: %s)", type(e).__name__, e)

    @staticmethod
    def cancel(task):
        if task and not task.done():
            task.cancel()

    def on_event(self, msg):
        kind = msg["notify"]
        channel = msg.get("param-channel-id", "")
        if kind == "power-mode-changed":
            log.info("TV power: %s", msg.get("param-power-mode"))
        else:
            log.debug("event %s %s", kind, channel)
        if kind == "power-mode-changed":
            self.powered_on = msg.get("param-power-mode") == "power-on"
            if self.powered_on:
                self.schedule_check("poweron", 1.5)
            else:
                self.cancel(self.pending)
                self.cancel(self.input_watch)
        elif kind == "plugin-ui-exit" and channel not in ("", "native-ui", LAUNCHER_ID):
            # An app closed. Leaving the launcher itself is skipped on purpose:
            # pressing Home there means "take me to Roku home".
            self.schedule_check("appexit", 0.2)
        elif kind == "plugin-ui-run" and channel not in ("", "native-ui", LAUNCHER_ID):
            # Another app is starting; nothing to redirect. (The launcher starting is
            # usually our own redirect, which must not cancel itself mid-launch.)
            self.cancel(self.pending)
            self.cancel(self.input_watch)

    async def after_reconnect(self, lost_for):
        """Events are missed while disconnected (deep sleep, TV restart, Wi-Fi drop).
        If the TV came back on in that gap, treat it as a power-on."""
        powered_on, uptime = await asyncio.to_thread(ecp_power, self.tv)
        if powered_on and (self.powered_on is False or uptime < lost_for + 30):
            log.info("TV turned on or restarted while disconnected; checking screen")
            self.schedule_check("wake", 0.5)
        self.powered_on = powered_on

    async def watch_events(self, retry_every=5):
        """Hold the event connection open. Retries stay short (not exponential) so
        a TV waking from deep sleep is picked up within seconds."""
        loop = asyncio.get_running_loop()
        lost_at = None
        while True:
            try:
                async with EcpSession(self.tv) as session:
                    if lost_at is None:
                        log.info("connected to %s, listening for events", self.tv)
                    else:
                        lost_for = loop.time() - lost_at
                        log.info("reconnected to %s after %.0fs", self.tv, lost_for)
                        lost_at = None
                        await self.after_reconnect(lost_for)
                    async for msg in session.events(EVENTS):
                        self.on_event(msg)
                reason = "closed by TV"
            except Exception as e:
                reason = f"{type(e).__name__}: {e}"
            if lost_at is None:
                lost_at = loop.time()
                log.info("event connection lost (%s); retrying every %ss", reason, retry_every)
            await asyncio.sleep(retry_every)


# ---------------------------------------------------------------- main

async def main(args):
    config = json.loads((ROOT / "config.json").read_text())
    listen = args.listen or config.get("listen", "0.0.0.0")
    port = args.port or config.get("port", 8099)
    # systemd's CacheDirectory= provides a writable cache (the home dir is read-only there)
    cache = Path(os.environ.get("CACHE_DIRECTORY", ROOT / "cache"))
    photos = Photos(ROOT / "photos", cache / "photos")
    weather = Weather(config.get("weather"))
    watcher = Watcher(config["tv"], args.redirect, photos, weather)
    log.info("screensaver: %d photos in %s; weather %s", len(photos.names()), photos.folder,
             "on" if "latitude" in (config.get("weather") or {}) else "not configured")
    server = await asyncio.start_server(watcher.handle_http, listen, port)
    log.info("launch relay listening on %s:%s", listen, port)
    tasks = [asyncio.create_task(server.serve_forever()),
             asyncio.create_task(asyncio.to_thread(photos.prewarm))]
    if args.redirect:
        tasks.append(asyncio.create_task(watcher.watch_events()))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--redirect", action="store_true", help="open the launcher on power-on / app exit")
    parser.add_argument("--listen", help="address to listen on (default: config.json listen, else 0.0.0.0)")
    parser.add_argument("--port", type=int, help="port (default: config.json port, else 8099)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every TV event")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.INFO)  # raw frames are too noisy
    asyncio.run(main(args))
