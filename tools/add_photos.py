"""Add photos to the watcher's library on the Pi.

    python tools/add_photos.py ~/Downloads/*-unsplash.jpg
    python tools/add_photos.py "dunes.jpg=Desert dunes" "city.jpg=City at night"

Each photo is downscaled to at most 3840px wide (4K; plenty for the TV, and
quicker for the Pi to decode), saved as JPEG under a short name taken from its
title, and uploaded to the Pi's photos/ folder. Its title and credit are merged
into photos.json there. Unsplash downloads (<photographer>-<photo id>-unsplash.jpg)
get the photographer and photo link filled in automatically.

The Pi prepares glass for new photos on its own; they appear in the wallpaper
picker immediately and in rotation as soon as they're ready.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
MAX_WIDTH = 3840
UNSPLASH = re.compile(r"^(?P<who>.+?)-(?P<id>[A-Za-z0-9_-]{11})-unsplash$")


def describe(path, title):
    """(file name, metadata) for a source photo and optional title."""
    meta = {}
    match = UNSPLASH.match(path.stem)
    if match:
        meta["credit"] = " ".join(w.capitalize() for w in match["who"].split("-"))
        meta["source"] = f"https://unsplash.com/photos/{match['id']}"
    if not title:
        title = f"Photo by {meta['credit']}" if "credit" in meta else path.stem.replace("-", " ").replace("_", " ")
    meta["title"] = title
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "photo"
    return f"{slug}.jpg", meta


def main(args):
    if not args:
        sys.exit(__doc__)
    config = json.loads((ROOT / "config.json").read_text())
    target, folder = config["pi"]["ssh"], config["pi"].get("dir", "horizon")

    meta = {}
    with tempfile.TemporaryDirectory() as tmp:
        for arg in args:
            source, _, title = arg.partition("=")
            path = Path(source).expanduser()
            name, info = describe(path, title)
            im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            if im.width > MAX_WIDTH:
                im = im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)), Image.LANCZOS)
            im.save(Path(tmp) / name, quality=90)
            meta[name] = info
            print(f"{name:28} {im.width}x{im.height}  {info['title']}" + (f"  ({info['credit']})" if "credit" in info else ""))

        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", *(str(Path(tmp) / n) for n in meta),
                        f"{target}:{folder}/photos/"], check=True)

    # Merge into photos.json on the Pi
    merge = (f"cd {folder}/photos && python3 -c \"import json, sys; p = 'photos.json'\n"
             "try: m = json.load(open(p))\nexcept OSError: m = {}\n"
             "m.update(json.load(sys.stdin)); json.dump(m, open(p, 'w'), indent=2)\"")
    subprocess.run(["ssh", "-o", "BatchMode=yes", target, merge], input=json.dumps(meta), text=True, check=True)
    print(f"added {len(meta)} photo(s)")


if __name__ == "__main__":
    main(sys.argv[1:])
