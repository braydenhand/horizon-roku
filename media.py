"""Photos, glass and weather for the launcher and screensaver, served by watcher.py.

Photos come from the photos/ folder next to the watcher, optionally with a
photos.json of {"file.jpg": {"title": ..., "credit": ...}}. Each photo is
rendered once to 1920x1080 and cached (the TV enlarges it slightly itself where
edges must stay off screen; textures larger than the screen break the Roku's
developer screenshots). Landscape shots are cropped to fill; portrait/square
shots sit on a blurred, dimmed copy of themselves.

Glass: the Roku can't blur live, so each glass surface (dock, inputs pill, ...)
is rendered here from a blurred copy of the home photo, cropped to exactly the
rectangle it covers on screen, so it lines up with the background behind it.

Weather comes from Open-Meteo (free, no API key), with today's sunrise and
sunset, which also decide whether bright (day) or dark (night) photos are
preferred.
"""
import datetime
import hashlib
import io
import json
import logging
import random
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

# Named render sizes (?size= on /photo). Only one is needed today.
SIZES = {"home": (1920, 1080)}
PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp"}
WEATHER_TTL = 15 * 60
WEATHER_STALE_OK = 3 * 60 * 60
TWILIGHT = datetime.timedelta(minutes=40)

log = logging.getLogger("watcher")

# WMO weather interpretation codes used by Open-Meteo
WMO_TEXT = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy",
    45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle", 61: "Light rain", 63: "Rain",
    65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain", 71: "Light snow",
    73: "Snow", 75: "Heavy snow", 77: "Snow", 80: "Showers", 81: "Showers",
    82: "Heavy showers", 85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorms", 96: "Thunderstorms", 99: "Thunderstorms",
}


# ---------------------------------------------------------------- rendering

def fit_photo(path, size):
    with Image.open(path) as im:
        # JPEGs decode straight to about screen size: ~1/3 the memory of a full decode
        im.draft("RGB", size)
        im = ImageOps.exif_transpose(im).convert("RGB")
    if im.width / im.height >= 1.3:
        return ImageOps.fit(im, size, Image.LANCZOS, centering=(0.5, 0.45))
    small = ImageOps.fit(im, (size[0] // 8, size[1] // 8), Image.BILINEAR)
    background = small.filter(ImageFilter.GaussianBlur(6)).resize(size, Image.BICUBIC)
    background = ImageEnhance.Brightness(background).enhance(0.5)
    photo = ImageOps.contain(im, size, Image.LANCZOS)
    background.paste(photo, ((size[0] - photo.width) // 2, (size[1] - photo.height) // 2))
    return background


def blur_field(photo):
    """What glass sees: the home photo, heavily blurred and a little more saturated."""
    return ImageEnhance.Color(photo.filter(ImageFilter.GaussianBlur(36))).enhance(1.25)


def rounded_mask(size, radius, supersample=4):
    w, h = size
    big = Image.new("L", (w * supersample, h * supersample), 0)
    ImageDraw.Draw(big).rounded_rectangle([0, 0, big.width - 1, big.height - 1],
                                          radius * supersample, fill=255)
    return big.resize(size, Image.LANCZOS)


def render_glass(field, x, y, w, h, radius, fade=None, fade_width=132):
    """A glass surface covering (x, y, w, h) on screen, as RGBA.

    fade="l"/"r" returns only the left/right fade_width pixels of that surface,
    with alpha ramping in from the inside: laid over the dock's contents it
    fades them into the glass at that edge.
    """
    glass = field.crop((x, y, x + w, y + h)).convert("RGBA")
    glass.alpha_composite(Image.new("RGBA", (w, h), (8, 14, 18, 70)))  # veil

    # Soft specular sheen toward the top-left
    sheen_alpha = Image.new("L", (w, h), 0)
    ImageDraw.Draw(sheen_alpha).ellipse([-w * 0.2, -h * 1.4, w * 0.6, h * 0.55], fill=28)
    sheen = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    sheen.putalpha(sheen_alpha.filter(ImageFilter.GaussianBlur(max(4, min(w, h) // 4))))
    glass.alpha_composite(sheen)

    # Light rim along the top, fading out down the sides
    rim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle([0, 0, w - 1, h - 1], radius, outline=(255, 255, 255, 62), width=2)
    rim_fade = Image.new("L", (1, h))
    for yy in range(h):
        rim_fade.putpixel((0, yy), max(0, round(255 * (1 - yy / max(1, h * 0.6)))))
    rim.putalpha(ImageChops.multiply(rim.getchannel("A"), rim_fade.resize((w, h))))
    glass.alpha_composite(rim)

    alpha = rounded_mask((w, h), radius)
    if fade in ("l", "r"):
        fw = min(fade_width, w)
        ramp = Image.new("L", (fw, 1))
        for xx in range(fw):
            t = xx / max(1, fw - 1) if fade == "r" else 1 - xx / max(1, fw - 1)
            ramp.putpixel((xx, 0), round(255 * 0.82 * t ** 1.4))
        left = w - fw if fade == "r" else 0
        glass = glass.crop((left, 0, left + fw, h))
        alpha = ImageChops.multiply(alpha.crop((left, 0, left + fw, h)), ramp.resize((fw, h)))
    glass.putalpha(alpha)
    return glass


# ---------------------------------------------------------------- photos

class Photos:
    def __init__(self, folder, cache):
        self.folder = Path(folder)
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # one render at a time keeps memory low
        self._brightness = {}
        # Glass shapes the TV has asked for, so every photo can be prepared ahead
        self._shapes_file = self.cache / "glass-shapes.json"
        try:
            self._shapes = {tuple(s) for s in json.loads(self._shapes_file.read_text())}
        except (OSError, ValueError):
            self._shapes = set()

    def names(self):
        if not self.folder.is_dir():
            return []
        return sorted(p.name for p in self.folder.iterdir()
                      if p.is_file() and p.suffix.lower() in PHOTO_TYPES and not p.name.startswith("."))

    def info(self, name):
        try:
            meta = json.loads((self.folder / "photos.json").read_text()).get(name, {})
        except (OSError, ValueError):
            meta = {}
        return {"title": meta.get("title", ""), "credit": meta.get("credit", "")}

    def _key(self, name, *parts):
        stat = (self.folder / name).stat()
        raw = "|".join(str(p) for p in (name, stat.st_mtime_ns, stat.st_size) + parts)
        return hashlib.sha1(raw.encode()).hexdigest()

    def _cached(self, path, make):
        """Return bytes at path, creating them with make() -> bytes under the lock."""
        if path.exists():
            return path.read_bytes()
        with self._lock:
            if not path.exists():
                started = time.monotonic()
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(make())
                tmp.replace(path)
                log.info("photos: rendered %s in %.1fs", path.name[:12], time.monotonic() - started)
        return path.read_bytes()

    def jpeg(self, name, size="home"):
        """Rendered JPEG bytes for a photo in the folder, or None if there is no such photo."""
        if name not in self.names() or size not in SIZES:  # also rules out path traversal
            return None
        dims = SIZES[size]

        def make():
            buf = io.BytesIO()
            fit_photo(self.folder / name, dims).save(buf, "JPEG", quality=88, optimize=True)
            return buf.getvalue()
        return self._cached(self.cache / f"{self._key(name, dims)}.jpg", make)

    def blurred(self, name):
        """JPEG bytes of the blurred home photo (what glass sees), or None."""
        if name not in self.names():
            return None

        def make():
            buf = io.BytesIO()
            blur_field(Image.open(io.BytesIO(self.jpeg(name, "home")))).save(buf, "JPEG", quality=92)
            return buf.getvalue()
        return self._cached(self.cache / f"{self._key(name, 'field')}.jpg", make)

    def _field(self, name):
        return Image.open(io.BytesIO(self.blurred(name))).convert("RGB")

    def glass(self, name, x, y, w, h, radius, fade=None, fade_width=132):
        """PNG bytes of a glass surface over this photo, or None if there is no such photo."""
        if name not in self.names():
            return None
        if not (0 <= x < 1920 and 0 <= y < 1080 and 0 < w <= 1920 - x and 0 < h <= 1080 - y):
            return None

        shape = (x, y, w, h, radius, fade, fade_width)
        if shape not in self._shapes:
            self._shapes.add(shape)
            self._shapes_file.write_text(json.dumps(sorted(self._shapes, key=str)))
            threading.Thread(target=self.prewarm, daemon=True).start()

        def make():
            buf = io.BytesIO()
            # Low compression: these travel over the LAN, and saving fast matters more
            render_glass(self._field(name), *shape).save(buf, "PNG", compress_level=1)
            return buf.getvalue()
        key = self._key(name, "glass", *shape)
        return self._cached(self.cache / f"{key}.png", make)

    def thumb(self, name):
        """Rounded 320x180 PNG preview for the wallpaper picker, or None."""
        if name not in self.names():
            return None
        return self._cached(self.cache / f"{self._key(name, 'thumb')}.png",
                            lambda: rounded_icon(self.jpeg(name, "home"), (320, 180), 20))

    def brightness(self, name):
        """Mean luminance (0-255) of the home rendering, remembered per file version."""
        key = self._key(name, "brightness")
        if key not in self._brightness:
            im = Image.open(io.BytesIO(self.jpeg(name, "home"))).convert("L").resize((96, 54))
            self._brightness[key] = ImageStat.Stat(im).mean[0]
        return self._brightness[key]

    def prewarm(self):
        """Render everything the TV will ask for, so the first requests are instant."""
        for name in self.names():
            try:
                self.jpeg(name, "home")
                self._field(name)
                self.brightness(name)
                for shape in list(self._shapes):
                    self.glass(name, *shape)
            except Exception as e:
                log.warning("photos: could not prepare %s (%s: %s)", name, type(e).__name__, e)


class Wallpapers:
    """Chooses photos for the time of day: brighter ones while the sun is up,
    darker ones at night, any of them around sunrise and sunset."""

    def __init__(self, photos, weather):
        self.photos = photos
        self.weather = weather
        self._last = None

    def period(self, now=None):
        now = now or datetime.datetime.now()
        today = now.date()
        sunrise = datetime.datetime.combine(today, datetime.time(7, 0))
        sunset = datetime.datetime.combine(today, datetime.time(19, 0))
        sun = (self.weather.current() or {}) if self.weather else {}
        try:
            sunrise = datetime.datetime.fromisoformat(sun["sunrise"])
            sunset = datetime.datetime.fromisoformat(sun["sunset"])
        except (KeyError, TypeError, ValueError):
            pass
        if abs(now - sunrise) <= TWILIGHT or abs(now - sunset) <= TWILIGHT:
            return "twilight"
        return "day" if sunrise < now < sunset else "night"

    def pool(self, now=None):
        names = sorted(self.photos.names(), key=self.photos.brightness)
        if len(names) < 4:
            return names
        half = len(names) // 2
        period = self.period(now)
        if period == "day":
            return names[half:]
        if period == "night":
            return names[:len(names) - half]
        return names

    def choose(self, now=None, pin=None):
        """A photo for Home: the pinned one if it still exists, else one that
        suits the time of day (not the same as last time)."""
        if pin and pin in self.photos.names():
            name = pin
        else:
            pool = self.pool(now)
            if not pool:
                return None
            options = [n for n in pool if n != self._last] or pool
            name = random.choice(options)
        self._last = name
        return {"name": name, "period": self.period(now),
                "photo": "/photo/" + urllib.parse.quote(name) + "?size=home",
                **self.photos.info(name)}


# ---------------------------------------------------------------- app icons

def rounded_icon(data, size=(288, 216), radius=24):
    """Roku's app artwork (290x218 JPEG) as a rounded-corner PNG for the All apps grid."""
    icon = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), size, Image.LANCZOS)
    icon.putalpha(rounded_mask(size, radius))
    buf = io.BytesIO()
    icon.save(buf, "PNG", compress_level=1)
    return buf.getvalue()


# ---------------------------------------------------------------- weather

class Weather:
    def __init__(self, config):
        self.config = config or {}
        self._cached = None
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def current(self):
        """Small dict for the TV, or None if not configured / unavailable."""
        if "latitude" not in self.config or "longitude" not in self.config:
            return None
        with self._lock:
            age = time.time() - self._fetched_at
            if self._cached and age < WEATHER_TTL:
                return self._cached
            try:
                self._cached = self._fetch()
                self._fetched_at = time.time()
            except Exception as e:
                log.warning("weather fetch failed (%s: %s)", type(e).__name__, e)
                if age > WEATHER_STALE_OK:
                    self._cached = None
            return self._cached

    def _fetch(self):
        params = {
            "latitude": self.config["latitude"],
            "longitude": self.config["longitude"],
            "current": "temperature_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset",
            "temperature_unit": self.config.get("unit", "fahrenheit"),
            "timezone": "auto",
            "forecast_days": 1,
        }
        url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
        current, daily = data["current"], data["daily"]
        return {
            "temp": round(current["temperature_2m"]),
            "high": round(daily["temperature_2m_max"][0]),
            "low": round(daily["temperature_2m_min"][0]),
            "text": WMO_TEXT.get(current["weather_code"], ""),
            "sunrise": daily["sunrise"][0],
            "sunset": daily["sunset"][0],
        }
