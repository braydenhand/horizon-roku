"""Build the launcher package and sideload it to the TV.

    python build.py              render art, package, install
    python build.py --no-install only build build/launcher.zip

Reads launcher.json (apps, marks and inputs to show) and config.json (TV
address, developer-mode login, and where the watcher runs; see
config.example.json). The live background and
its glass come from the watcher on the Pi; the package carries one default
photo (assets/wallpapers/default.jpg, or the first photo in wallpapers/) with
matching glass for when the Pi can't be reached.
"""
import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import resvg_py
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from media import blur_field, render_glass, rounded_mask

ROOT = Path(__file__).resolve().parent
APP_SRC = ROOT / "app"
ASSETS = ROOT / "assets"
BUILD = ROOT / "build"
PKG = BUILD / "pkg"
ZIP_PATH = BUILD / "launcher.zip"

W, H = 1920, 1080
FONTS = ["Inter-Light.ttf", "Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf"]

# Home screen geometry (CleanFrame's proportions). Shared with the TV through
# data/apps.json, which also uses it to request matching glass from the Pi.
DOCK = {"x": 208, "y": 644, "w": 1504, "h": 232, "r": 48}
TILE = {"w": 288, "h": 200, "r": 32, "inset": 16}  # tiles sit 16px inside the dock
FADE_WIDTH = 132
MARK_BOX = (132, 64)
SEGMENT = {"w": 184, "h": 64, "r": 32, "pad": 8}  # one input inside the pill
PILL_Y, PILL_H = 916, 80
MINI = 80
# Top-right controls: glass circles that appear behind the focused icon
UTILS = [("apps", "All apps", "apps"), ("art", "Screensaver", "saver"), ("settings", "Settings", "settings")]  # icon, label, action
UTIL_R, UTIL_CY, UTIL_RIGHT, UTIL_STEP = 36, 104, 1780, 96
GRID = {"cols": 5, "cellW": 336, "cellH": 316, "left": 120, "top": 232}


def layout(input_count):
    shelf_w = DOCK["w"] - 2 * TILE["inset"]
    pill_w = 2 * SEGMENT["pad"] + input_count * SEGMENT["w"]
    return {
        "dock": [DOCK["x"], DOCK["y"], DOCK["w"], DOCK["h"], DOCK["r"]],
        "shelf": [DOCK["x"] + TILE["inset"], DOCK["y"], shelf_w, DOCK["h"]],
        "tile": [TILE["w"], TILE["h"], TILE["inset"]],
        "visible": shelf_w // TILE["w"],
        "fade": FADE_WIDTH,
        "pill": [(W - pill_w) // 2, PILL_Y, pill_w, PILL_H, PILL_H // 2],
        "mini": [(W - MINI) // 2, PILL_Y, MINI, MINI, MINI // 2],
        "segment": [SEGMENT["w"], SEGMENT["h"], SEGMENT["pad"]],
        "utils": [util_entry(i, *u) for i, u in enumerate(UTILS)],
        "grid": GRID,
    }


def util_entry(i, icon, label, action):
    cx = UTIL_RIGHT - UTIL_R - (len(UTILS) - 1 - i) * UTIL_STEP
    d = 2 * UTIL_R
    return {"rect": [cx - UTIL_R, UTIL_CY - UTIL_R, d, d, UTIL_R], "icon": f"pkg:/images/marks/{icon}.png",
            "label": label, "action": action, "glass": f"pkg:/images/glass_util{i}.png"}


# ---------------------------------------------------------------- artwork

def svg_to_image(path, height):
    """White mark trimmed to its visible pixels, height px tall."""
    big = Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(
        svg_string=path.read_text(), height=512)))).convert("RGBA")
    # Some sources (Simple Icons) have no fill and render black: keep only the shape
    white = Image.new("RGBA", big.size, (255, 255, 255, 0))
    white.putalpha(big.getchannel("A"))
    bbox = white.getchannel("A").getbbox()
    white = white.crop(bbox) if bbox else white
    return white.resize((max(1, round(white.width * height / white.height)), height), Image.LANCZOS)


def fit_mark(path, box):
    """Scale a mark to fit inside box (w, h) without distortion."""
    im = svg_to_image(path, box[1])
    if im.width > box[0]:
        im = im.resize((box[0], max(1, round(im.height * box[0] / im.width))), Image.LANCZOS)
    return im


def vertical_gradient(size, stops, color=(4, 10, 12)):
    """Black-ish layer whose alpha follows stops [(position 0-1, alpha 0-1), ...]."""
    column = Image.new("L", (1, size[1]))
    for y in range(size[1]):
        t = y / max(1, size[1] - 1)
        for (t0, a0), (t1, a1) in zip(stops, stops[1:]):
            if t0 <= t <= t1:
                column.putpixel((0, y), round(255 * (a0 + (a1 - a0) * (t - t0) / max(1e-6, t1 - t0))))
                break
    layer = Image.new("RGBA", size, color + (0,))
    layer.putalpha(column.resize(size))
    return layer


def make_plate(w, h, r):
    """Focus plate: translucent white, brighter at the top, with a rim that
    follows the top corners and fades out down the sides."""
    column = Image.new("L", (1, h))
    for y in range(h):
        t = y / h
        a = 0.30 - 0.14 * min(t / 0.58, 1) + 0.06 * max(0.0, t - 0.58) / 0.42
        column.putpixel((0, y), round(255 * a))
    plate = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    plate.putalpha(ImageChops.multiply(column.resize((w, h)), rounded_mask((w, h), r)))
    rim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle([0, 0, w - 1, h - 1], r, outline=(255, 255, 255, 110), width=2)
    rim_fade = Image.new("L", (1, h))
    for y in range(h):
        rim_fade.putpixel((0, y), max(0, 255 - round(y * 255 / (h * 0.45))))
    rim.putalpha(ImageChops.multiply(rim.getchannel("A"), rim_fade.resize((w, h))))
    plate.alpha_composite(rim)
    return plate


def make_scrim(size=(325, 135), strength=0.62):
    """Soft shadow in the bottom-left corner behind the screensaver's clock.
    Stored at quarter size; the Poster stretches it to 1300x540."""
    sw, sh = max(1, size[0] // 4), max(1, size[1] // 4)
    alpha = Image.new("L", (sw, sh))
    for y in range(sh):
        for x in range(sw):
            d = ((x / sw) ** 2 + ((sh - y) / sh) ** 2) ** 0.5
            t = min(max((d - 0.15) / 0.85, 0.0), 1.0)
            alpha.putpixel((x, y), int(255 * strength * (1 - t * t * (3 - 2 * t))))
    scrim = Image.new("RGBA", size, (0, 0, 0, 0))
    scrim.putalpha(alpha.resize(size, Image.BICUBIC))
    return scrim


def draw_wordmark(image, text, size, tracking=-0.02):
    """App name centered on image: Inter SemiBold, slightly tightened, with a
    soft shadow so it reads on any photo."""
    font = ImageFont.truetype(str(ASSETS / "fonts" / "Inter-SemiBold.ttf"), size)
    advances = [font.getlength(ch) + size * tracking for ch in text]
    width = sum(advances) - size * tracking
    x = (image.width - width) / 2
    y = image.height / 2
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ink = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for ch, advance in zip(text, advances):
        ImageDraw.Draw(shadow).text((x, y + size * 0.03), ch, font=font, fill=(0, 0, 0, 90), anchor="lm")
        ImageDraw.Draw(ink).text((x, y), ch, font=font, fill=(246, 241, 231, 255), anchor="lm")
        x += advance
    out = image.convert("RGBA")
    out.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(size * 0.12)))
    out.alpha_composite(ink)
    return out.convert("RGB")


def make_splash(wallpaper, title):
    """Launch screen: the default photo blurred like the glass, with the name."""
    backdrop = ImageEnhance.Brightness(blur_field(wallpaper)).enhance(0.82)
    return draw_wordmark(backdrop, title, 132)


def make_poster(wallpaper, title, size):
    """App tile in Roku's app list, matching the launch screen."""
    backdrop = ImageEnhance.Brightness(blur_field(ImageOps.fit(wallpaper, size, Image.LANCZOS))).enhance(0.82)
    return draw_wordmark(backdrop, title, size[1] // 4)


def default_wallpaper():
    photos = sorted(p for p in (ROOT / "wallpapers").glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    source = photos[0] if photos else ASSETS / "wallpapers" / "default.jpg"
    return ImageOps.fit(Image.open(source).convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, 0.45))


# ---------------------------------------------------------------- package

def build(config, launcher):
    if PKG.exists():
        shutil.rmtree(PKG)
    shutil.copytree(APP_SRC, PKG)
    images = PKG / "images"
    (images / "marks").mkdir(parents=True)
    (PKG / "fonts").mkdir()
    (PKG / "data").mkdir()
    for font in FONTS:
        shutil.copy(ASSETS / "fonts" / font, PKG / "fonts" / font)

    geo = layout(len(launcher["inputs"]))

    # Default background and its glass, for when the Pi is unreachable
    wallpaper = default_wallpaper()
    wallpaper.save(images / "wallpaper.jpg", quality=90)
    make_splash(wallpaper, launcher["title"]).save(images / "splash.jpg", quality=90)
    field = blur_field(wallpaper)
    for name, rect, fade in [("dock", geo["dock"], None), ("fadeL", geo["dock"], "l"),
                             ("fadeR", geo["dock"], "r"), ("pill", geo["pill"], None),
                             ("mini", geo["mini"], None)]:
        render_glass(field, *rect, fade=fade, fade_width=FADE_WIDTH).save(images / f"glass_{name}.png", optimize=True)
    for i, util in enumerate(geo["utils"]):
        render_glass(field, *util["rect"]).save(images / f"glass_util{i}.png", optimize=True)
        svg_to_image(ASSETS / "marks" / f"{UTILS[i][0]}.svg", 36).save(images / "marks" / f"{UTILS[i][0]}.png")
    field.save(images / "wallpaper_blur.jpg", quality=88)

    # Vertical gradients: a few pixels wide, stretched across the screen by the
    # Poster (image memory on the TV is ~100 MB shared with Roku's own UI)
    vertical_gradient((4, 300), [(0, 0.34), (0.5, 0.14), (1, 0)]).save(images / "shade_top.png")
    vertical_gradient((4, 520), [(0, 0), (0.7, 0.40), (1, 0.50)]).save(images / "scrim_bottom.png")
    make_plate(TILE["w"], TILE["h"], TILE["r"]).save(images / "plate_tile.png")
    make_plate(SEGMENT["w"], SEGMENT["h"], SEGMENT["r"]).save(images / "plate_segment.png")
    make_plate(GRID["cellW"] - 16, GRID["cellH"] - 16, TILE["r"]).save(images / "plate_grid.png")
    make_plate(1056, 112, 20).save(images / "plate_row.png")      # Settings rows
    make_plate(400, 252, TILE["r"]).save(images / "plate_pick.png")  # wallpaper picker cells
    make_plate(320, 180, 20).save(images / "thumb_auto.png")      # the "Automatic" choice
    svg_to_image(ASSETS / "marks" / "tv.svg", 36).save(images / "tv.png")
    make_scrim().save(images / "scrim.png")
    make_poster(wallpaper, launcher["title"], (290, 218)).save(images / "poster_hd.jpg", quality=90)
    make_poster(wallpaper, launcher["title"], (540, 405)).save(images / "poster_fhd.jpg", quality=90)

    apps = []
    for app in launcher["apps"]:
        entry = {"id": app["id"], "name": app["label"]}
        if app.get("mark"):
            mark = fit_mark(ASSETS / "marks" / f"{app['mark']}.svg", MARK_BOX)
            mark.save(images / "marks" / f"{app['mark']}.png")
            entry.update(mark=f"pkg:/images/marks/{app['mark']}.png", markW=mark.width, markH=mark.height)
        apps.append(entry)
    inputs = [{"id": i["id"], "name": i["label"]} for i in launcher["inputs"]]

    # Default dock order, plus display names and marks by app id for apps added
    # to the dock later from All apps
    data = {"layout": geo, "apps": apps, "catalog": {a["id"]: a for a in apps}, "inputs": inputs,
            "relay": f"http://{config['pi']['address']}:{config['pi'].get('port', 8099)}" if config.get("pi") else ""}
    (PKG / "data" / "apps.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # A changing build number stops the TV from skipping an "identical" upload
    manifest = (PKG / "manifest").read_text()
    manifest = re.sub(r"(?m)^title=.*$", f"title={launcher['title']}", manifest)
    manifest = re.sub(r"(?m)^build_version=.*$", f"build_version={int(time.time()) % 100000}", manifest)
    (PKG / "manifest").write_text(manifest)

    ZIP_PATH.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(PKG.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(PKG).as_posix())
    print(f"built {ZIP_PATH} ({ZIP_PATH.stat().st_size // 1024} KB, {len(apps)} apps, {len(inputs)} inputs)")


def install(config):
    result = subprocess.run(
        ["curl", "-sS", "--digest", "-u", f"{config['dev_user']}:{config['dev_password']}",
         "-F", "mysubmit=Install", "-F", f"archive=@{ZIP_PATH}",
         f"http://{config['tv']}/plugin_install"],
        capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        sys.exit(f"install failed: {result.stderr.strip()}")
    messages = re.findall(r"(Install Success[^<\"']*|Install Failure[^<\"']*|Identical to previous version[^<\"']*)",
                          result.stdout)
    print("install:", "; ".join(dict.fromkeys(messages)) or result.stdout[:500])
    if any("Failure" in m for m in messages):
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-install", action="store_true", help="build the zip only")
    args = parser.parse_args()

    config = json.loads((ROOT / "config.json").read_text())
    launcher = json.loads((ROOT / "launcher.json").read_text())
    build(config, launcher)
    if not args.no_install:
        install(config)


if __name__ == "__main__":
    main()
