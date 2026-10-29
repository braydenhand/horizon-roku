# Developing Horizon

Everything a person or coding agent needs to change Horizon safely: how the pieces fit, the Roku behaviour it depends on (much of it undocumented), the traps we fell into, and how it got here.

- [Architecture](#architecture)
- [Roku platform findings](#roku-platform-findings)
- [The Roku app](#the-roku-app)
- [The watcher](#the-watcher)
- [Build & assets](#build--assets)
- [Workflow, testing & debugging](#workflow-testing--debugging)
- [SceneGraph gotchas we hit](#scenegraph-gotchas-we-hit)
- [History](#history)
- [Ideas](#ideas)

---

## Architecture

```
            ┌──────────────────────── Roku TV ────────────────────────┐
            │  Horizon (dev app)            Roku OS                  │
            │  HomeScene, AllApps,          ECP  :8060  (HTTP + WS)   │
            │  Settings, PhotoFrame         dev installer :80, console :8085
            └──────▲───────────────┬──────────────▲──────────┬───────┘
     HTTP :8099    │ wallpaper,    │ launch        │ events   │ launch dev,
     (LAN only)    │ glass, apps,  │ (roAppManager)│ (ECP-2)  │ tvinput.* (HTTP ECP)
                   │ icons, input  │               │          │
            ┌──────┴───────────────▼──────────────┴──────────▼───────┐
            │  Pi: watcher.py (systemd "roku-watcher")               │
            │   ├─ Watcher: event loop, redirect, input polling      │
            │   ├─ HTTP routes (launch relay, media API)             │
            │   └─ media.py: Photos, Wallpapers, Weather             │
            │  ~/<dir>/photos  (+ photos.json)   /var/cache/roku-watcher
            └────────────────────────────────────────────────────────┘
```

**Responsibilities**

| Concern | Where | Why there |
|---|---|---|
| Drawing the UI, focus, animation | Roku app | Only the app can draw |
| Opening other apps | Roku app (`roAppManager.LaunchApp`) | Works from inside an app, no network |
| Switching HDMI / Live TV | Watcher (HTTP ECP `/launch/tvinput.*`) | `LaunchApp` refuses inputs; ECP from the TV itself is refused |
| Reopening Horizon (power‑on, app exit, input exit) | Watcher | Apps can't run in the background or see Home |
| Installed app list and icons | Watcher (ECP‑2) | The app can't query ECP about itself |
| Blur / glass | Watcher (Pillow), pre‑rendered per photo | SceneGraph has no live blur |
| Geometry (dock, pill, buttons, grid) | `build.py` → `data/apps.json` | One source of truth for both app and glass requests |

### HTTP API (watcher, default port 8099)

| Route | Returns |
|---|---|
| `POST /launch/<id>` | Launches `<id>` via HTTP ECP (used for `tvinput.*`). Starts input polling for inputs |
| `GET /health` | `ok` |
| `GET /wallpaper[?pin=<name>]` | `{"name","photo","title","credit","period"}`: a photo for the time of day (not the previous one), or the pinned one |
| `GET /photos[?auto=1]` | `{"photos": ["/photo/<name>", …]}` (all, or the current day/night pool) |
| `GET /photo/<name>[?size=home]` | 1920×1080 JPEG |
| `GET /blur/<name>` | Blurred 1920×1080 JPEG (what glass "sees"; sheet backdrops) |
| `GET /glass/<name>?x&y&w&h&r[&fade=l\|r&fw=]` | RGBA PNG of a glass surface covering that screen rectangle |
| `GET /gallery`, `GET /thumb/<name>` | Picker data and 320×180 rounded thumbnails |
| `GET /apps`, `GET /icon/<id>` | Installed apps (by name, excluding inputs and `dev`) and rounded 288×216 icons |
| `GET /weather` | `{"temp","high","low","text","sunrise","sunset"}` (Open‑Meteo, cached 15 min) |

### Redirect logic (watcher)

- **Power on** (`power-mode-changed` → `power-on`): after 1.5 s, check the screen. Roku home → launch Horizon. On an input → start input polling.
- **App exit** (`plugin-ui-exit` for anything except `dev`/`native-ui`): after 0.2 s, check up to 8 × 0.35 s. Roku home **or a blank reading** → launch Horizon (the TV reports no app for a moment on its way home). Leaving **Horizon itself** is ignored on purpose: pressing Home there means "take me to Roku home".
- **Another app starts** (`plugin-ui-run`, not `dev`): cancel any pending check.
- **Inputs** send no events at all, so while on HDMI/Live TV the watcher polls `query-active-app` every 0.75 s. It *arms* only after actually seeing the input (the TV briefly reports Roku home or nothing while switching), gives up after 10 s if the input never appears, and needs two consecutive Roku‑home readings before redirecting.
- **Connection drops** (deep sleep, TV restart, Wi‑Fi): retry every 5 s. After reconnecting, read `device-info` (`power-mode`, `uptime`); if the TV was off and is now on, or restarted during the gap, treat it as a power‑on.

---

## Roku platform findings

Verified on a Hisense 65" 6‑Series (G211X), Roku OS 15.3.4, with *Settings → System → Advanced → Control by mobile apps* on its default **Limited**.

### ECP over HTTP (port 8060) in Limited mode

| Request | Result |
|---|---|
| `GET /query/device-info` | ✅ works (power mode, uptime, model…) |
| `GET /query/active-app` | ⚠️ answers, but always "Roku" (useless) |
| `GET /query/apps`, `/query/icon/<id>`, `/query/media-player`, `/query/chanperf` | ❌ 403 "ECP command not allowed in Limited mode" |
| `POST /keypress/<key>` | ❌ 403 |
| `POST /launch/<id>` **from another device** | ✅ works, including `tvinput.hdmi1` and `dev` (`?reason=…` arrives as a launch parameter) |
| `POST /launch/<id>` **from an app on the TV** (its own IP or 127.0.0.1) | ❌ 403 "ECP is not allowed from loopback address" |

### ECP‑2 WebSocket: `ws://<tv>:8060/ecp-session`, subprotocol `ecp-2`

What Roku's mobile app uses, and it is **not** restricted by Limited mode.

1. The TV sends `{"notify":"authenticate","param-challenge":"<b64>"}`.
2. Reply `{"request":"authenticate","request-id":"1","param-response": base64(sha1(challenge + KEY))}`, where `KEY` is the mobile app's GUID `95E610D0-7C29-44EF-FB0F-97F1FCE4C297` with each hex digit `v` mapped to `(15 - v + 9) & 15` (see `roku_ecp._transform_key`).
3. Requests: `{"request": "<name>", "request-id": "<n>", "param-…": …}`. Answers carry `response-id`, `status` and base64 `content-data` (usually the same XML as HTTP ECP).

| Request | Notes |
|---|---|
| `query-apps`, `query-icon` (`param-channel-id`), `query-active-app`, `query-media-player`, `query-device-info`, `query-chanperf`, `query-r2d2-bitmaps` | All work in Limited mode. `query-active-app` is accurate here: Roku home is `type="home"` (`Roku Dynamic Menu`), inputs are `type="tvin"` with ids `tvinput.*` |
| `launch` (`param-channel-id`) | Works |
| `key-press` (`param-key`: `Up`, `Down`, `Left`, `Right`, `Select`, `Back`, `Home`, `Info` = ✱ …) | Works; used by `tools/remote.py` for testing |
| `request-events` (`param-events`: `+power-mode-changed,+plugin-ui-run,+plugin-ui-exit,…`) | Push notifications. Observed: `power-mode-changed` (`power-on` / `display-off`), `plugin-ui-run`, `plugin-ui-run-script`, `plugin-ui-exit`, `media-player-state-changed`. **No event** for switching between Roku home and inputs; both are the `native-ui` channel |

- The TV pings the socket about every 10 s; `websockets` answers automatically.
- **Too many simultaneous sessions → HTTP 503.** The watcher keeps one long‑lived event session and serializes short‑lived ones (e.g. icon fetches).
- In **warm standby** (`display-off`) the socket stays open. Deep sleep or restarts close it; see reconnect logic above.

### Inside the app

- `roAppManager.LaunchApp(id, "", {})` opens normal apps from a sideloaded app. It returns `false` for `tvinput.*` (with any version string), hence the relay.
- The developer installer: `POST http://<tv>/plugin_install` (digest auth `rokudev:<password>`, `mysubmit=Install`, `archive=@zip`), and screenshots via `POST /plugin_inspect` (`mysubmit=Screenshot`), then `GET /pkgs/dev.jpg`.
- Debug console: `telnet <tv> 8085`. **One connection at a time.** Prints, compile errors, and the debugger on a crash.
- **Image memory is about 100 MB, shared with Roku's own UI, and acts as a cache.** `query-r2d2-bitmaps` lists every texture. Images held by hidden nodes stay loaded. When it's nearly full, developer screenshots come back black (the UI itself keeps working, because unreferenced textures get evicted).
- Textures larger than the UI resolution log a warning and also broke screenshots, so photos are served at exactly 1920×1080 and enlarged ~3% by the Poster where edges must stay off screen.
- Screensavers: `screensaver_title=` in the manifest plus `sub RunScreenSaver()` (in `source/screensaver.brs`). A developer app's screensaver can be selected under *Settings → Theme → Screensavers*, and it runs over the app itself too. It must not use component code (see "Debugging the screensaver" below).

---

## The Roku app

```
app/source/main.brs           Main (HomeScene)
app/source/screensaver.brs    RunScreenSaver: drives ScreensaverScene from its own thread
app/source/frame.brs          clock/date text and shuffle, shared with PhotoFrame
app/components/
  HomeScene.xml/.brs          home screen, dock, inputs pill, top-right controls,
                              wallpaper loading/rotation, sheet + screensaver layers
  Tile.xml/.brs               one dock app: mark+label or name; highlight, lift, slide
  AllApps.xml/.brs            All apps sheet (grid, ✱ to add/remove)
  Settings.xml/.brs           Settings sheet + wallpaper picker
  PhotoFrame.xml/.brs         photo crossfade + clock (in-app screensaver)
  ScreensaverScene.xml        Roku screensaver: the same nodes, no script
  LaunchTask.xml/.brs         LaunchApp, or the watcher relay for inputs
  FetchTask.xml/.brs          HTTP GET in a task thread
```

**HomeScene zones** (`m.zone`): `dock`, `inputs`, `utils`, `move`, `sheet`, `saver`. `onKeyEvent` dispatches by zone. Remote focus sits on an invisible `keyTrap` Group (keys bubble to the scene); sheets take focus while open.

**Startup.** `bgA` shows `splash.jpg` (the same image as the system splash, so the hand‑off is seamless). `/wallpaper` → the photo goes into `bgB` and five glass surfaces load (dock, two dock fades, pill, mini circle; the three button circles load without blocking). Once everything is ready, `bgB` fades in with the content. After 2 s without the Pi, the bundled `wallpaper.jpg` and bundled glass are used instead.

**Wallpaper changes** (timed or from Settings): the new photo and its glass load into an invisible `preload` group, then the photo crossfades on whichever background poster is hidden, and the glass swaps from cache. Afterwards the covered poster and the preloads are cleared to free image memory.

**Saved state** (`roRegistrySection("home")`): `dock` (JSON `[{id,name}]`), `input` (index), `wallPin` (photo file name or ""), `wallEvery` (`open` / `15` / `60`).

---

## The watcher

`watcher.py` (asyncio) + `media.py` (Pillow) + `roku_ecp.py`.

- **Systemd unit** from `pi/roku-watcher.service.in` (installed by `deploy_pi.py`): runs as your user, starts after the network (deliberately not after any VPN), restarts forever, read‑only home folder (`ProtectHome=read-only`), writable cache via `CacheDirectory=roku-watcher` (`$CACHE_DIRECTORY`), `MemoryMax=192M`.
- **Rendering & caching:** everything is cached on disk under a key built from the file name, mtime and size, so changing a photo re‑renders it. One render at a time (a lock) keeps memory down; JPEGs are decoded at screen size via `Image.draft` (a 3840 px photo peaks around 50 MB instead of about 145 MB).
- **Prewarm:** on start, and whenever the TV asks for a glass shape it hasn't seen (shapes are remembered in `glass-shapes.json`), every photo gets its home render, blur, brightness and all known glass shapes. New photos are ready within seconds.
- **Day/night:** photos are sorted by mean luminance. Day → the brighter half, night → the darker half, ±40 min around sunrise/sunset → all. Sunrise and sunset come from the weather fetch (fallback 7:00/19:00).
- **Logs:** `journalctl -u roku-watcher`. Add `-v` to `ExecStart` for every TV event.

---

## Build & assets

`build.py`:
1. Copies `app/` → `build/pkg/`.
2. Renders: white marks from SVG (resvg; recoloured to white, since Simple Icons ships black), focus plates, the splash and app‑list poster (blurred default photo + wordmark), thin gradient strips (stretched by Posters to save image memory), fallback glass for the default photo, and the tv/control icons.
3. Writes `data/apps.json`: `layout` (all geometry), `apps` (default dock), `catalog` (names and marks by app id), `inputs`, `relay` (the watcher URL).
4. Bumps `build_version` (otherwise the TV may skip an "identical" upload), zips, and sideloads.

Geometry lives at the top of `build.py`. Glass rectangles are requested by the app using that same geometry, and the Pi renders whatever rectangle it's asked for, so changing the layout needs no Pi change.

---

## Workflow, testing & debugging

```bash
python build.py                      # build + install on the TV (the app restarts)
python deploy_pi.py                  # update the watcher
python tools/console.py 30           # TV debug console
python tools/listen.py 120           # live TV events
python tools/ws_query.py query-active-app query-apps
python tools/remote.py Up Right shot:check   # press keys, save build/shot-check.jpg
python tools/debugserver.py "sgnodes all" loaded_textures   # SceneGraph debug server (:8080)
python deploy_pi.py --verbose        # watcher logs every TV event and HTTP request
ssh <pi> journalctl -u roku-watcher -f
```

**Debugging the screensaver.** Roku's screensaver mode can't be screenshotted or inspected (`Channel not running: active UI` from both ECP‑2 and the debug server). Instead:
- `curl -X POST "http://<tv>:8060/launch/dev?mode=screensaver"` runs the screensaver (`RunScreenSaver`) as a normal app, so it can be screenshotted and inspected.
- With `deploy_pi.py --verbose`, a running screensaver shows up in the Pi's log as `GET /photos?auto=1` plus a `/photo/…` request within a second of starting.

**Solved (Oct 2026): a black screensaver over Horizon.** The **first** screensaver run in a Horizon session worked, but after it was dismissed, **no component code runs in later runs**: `RunScreenSaver` starts and its scene's nodes are created, but `init()`, field observers and Task nodes never execute, so nothing sets a photo or the clock and the screen stays black. (A first fix moved only the fetching out of Tasks; that looked verified because the requests still arrived, but the frame never received them.) Horizon's own components are unaffected. Fix: `ScreensaverScene` has **no component script**, just nodes, and `source/screensaver.brs` drives them from `RunScreenSaver`'s thread: `roUrlTransfer` for data, Poster `loadStatus` observed on its message port, a 5‑second `wait()` loop for the clock and photo timing, and Animation nodes started by setting `control`. The in‑app screensaver (Home's Screensaver button) still uses `PhotoFrame`; the two share `source/frame.brs`. Verified over several consecutive cycles, each loading a photo and crossfading to the next a minute later.

A misdiagnosis along the way: at first it looked like Roku never runs an app's *non‑private* screensaver while that app is open, so Horizon's was briefly made private (`screensaver_private=1`, which runs over the app but drops it from the Settings list). Once the component‑code problem was fixed, the normal (non‑private) screensaver worked over Horizon too, so the private flag was removed.

How this was diagnosed: the screensaver's output doesn't reach the debug console, so temporary pings sent `GET /health?saver=<state>` to the watcher (with `--verbose` on, the Pi logs every request) from `RunScreenSaver` and from the component (`init`, data received, poster load status). In later runs only `RunScreenSaver`'s pings arrived, and a field set in `init()` was still empty. **Check for actual `/photo/…` requests, not just `/photos`, when verifying the screensaver.**

Also not available on Roku OS 15: `roAppManager.UpdateLastKeyPressTime` and `GetScreensaverTimeout` (an app can't hold off the screensaver or read its delay).

**Remote testing etiquette.** `tools/remote.py` presses real buttons on a real TV. If focus isn't where you think it is, **OK launches whatever is focused**, and this happened twice during development. Take a `shot:` and look at it before any OK, and keep sequences short.

**A frozen launcher** (remote ignored) has almost always been a BrightScript runtime error: the app pauses in the debugger. `tools/console.py` shows the line, the stack and local variables. `python build.py` restarts it.

**Network safety on the Pi.** The Pi may run other important services (DNS filtering, VPNs). The watcher needs no network changes; `pi/netstate.sh` prints routes, rules, firewall and listening ports so you can diff before and after changes on the Pi.

---

## SceneGraph gotchas we hit

1. **Interpolators bind to the first node they resolve.** `fieldToInterp="saver.opacity"` kept animating a removed node after a new one with the same id was created. Animate a **permanent container** and swap children inside it.
2. **A hidden node can keep remote focus.** After closing a sheet, its `onKeyEvent` still received keys and launched apps. Give focus explicitly to a holder (`keyTrap`), call `setFocus(false)` on the sheet, *and* make the sheet ignore keys unless it's open.
3. **Find every node you use.** A missing `findNode` gives `invalid`, and assigning to its field crashes with "Invalid value for left‑side of expression" (that's how the photo change crashed: `bgA` was never looked up).
4. **No `const`** in BrightScript (that's BrighterScript).
5. **`ParseJson("")` logs an error**; check for empty strings first.
6. **Missing glyphs render as boxes.** Inter has ◀ ▶ ▼ ✓ · ° but not ✱; check with fontTools before using symbols.
7. **Hidden images still use memory.** Clear `uri` on posters that are covered or hidden (backdrops, the covered background, preloads, grids in closed sheets).
8. **Slow continuous zooms judder** (sub‑pixel steps). Use short eased motion. **Scaled image edges blend into black**, so overhang the screen.
9. **Stop a component's timers before removing it** (`PhotoFrame.active = false`), so nothing keeps firing in the background.

---

## History

Built in one long session on 24 Oct 2026, in this order:

1. **Exploration.** CleanFrame (Samsung) as the reference. Roku has no home‑screen replacement API, so the plan became a launcher plus a companion service.
2. **ECP discovery.** Limited mode blocks most HTTP ECP. The mobile app's ECP‑2 WebSocket works and gives push events. Launching over the network works.
3. **First launcher** (colourful app icons, input pills), then app launching: in‑app ECP returned 403 ("loopback"), while `roAppManager.LaunchApp` worked for apps but not inputs → the **launch relay** on the Pi.
4. **Watcher on the Pi**, a sandboxed systemd service added next to existing network services without touching their configuration (network state was diffed before and after).
5. **Redirect tuning:** no `native-ui` event after app exit (check the screen instead); inputs send no events (arming poller); deep‑sleep reconnects and wake detection.
6. **Screensaver** with photos and weather from the Pi; fixed a drifting shadow "border" and judder (short settle instead of a long zoom).
7. **Redesign** after CleanFrame: Unsplash photos, pre‑rendered liquid glass, white marks, gliding plate, inputs pill, day/night photos. Launch time went from 6.5 s to 3.3 s by pre‑rendering glass and acting on the blank in‑between reading.
8. **Top‑right controls:** All apps, Screensaver, Settings. **Dock editing** (✱), **wallpaper picker and rotation**.
9. **Name and splash: "Horizon".** Photo tooling, memory tuning (JPEG draft decode; releasing hidden images), docs.

## Ideas

- Rename inputs on the TV (e.g. "PS5") from Settings.
- Page the wallpaper picker for very large libraries (one thumbnail texture per photo).
- Optional captions (photo title and photographer); `photos.json` already has them.
- Clean up the render cache for photos that have been deleted.
- A "Roku Home" button in the top‑right controls.
