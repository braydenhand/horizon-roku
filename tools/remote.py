"""Press remote buttons over the ECP-2 WebSocket and grab dev screenshots.

    python tools/remote.py Up shot:utils OK wait:2 shot:sheet Back

Steps: a key name (Up, Down, Left, Right, Select/OK, Back, Home, ...),
wait:<seconds>, or shot:<name> to save build/shot-<name>.jpg (dev app only).
"""
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from roku_ecp import EcpSession  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text())
KEYS = {"OK": "Select"}


def screenshot(name):
    auth = ["--digest", "-u", f"{CONFIG['dev_user']}:{CONFIG['dev_password']}"]
    html = subprocess.run(["curl", "-s", *auth, "-F", "mysubmit=Screenshot", "-F", "passwd=", "-F", "archive=",
                           f"http://{CONFIG['tv']}/plugin_inspect"], capture_output=True, text=True).stdout
    match = re.search(r"pkgs/dev\.(jpg|png)[^\"']*", html)
    out = ROOT / "build" / f"shot-{name}.jpg"
    subprocess.run(["curl", "-s", *auth, "-o", str(out), f"http://{CONFIG['tv']}/{match.group(0)}"], check=True)
    print("saved", out)


async def main(steps):
    async with EcpSession(CONFIG["tv"]) as tv:
        for step in steps:
            if step.startswith("wait:"):
                await asyncio.sleep(float(step[5:]))
            elif step.startswith("shot:"):
                await asyncio.sleep(0.6)  # let animations settle
                await asyncio.to_thread(screenshot, step[5:])
            else:
                reply = await tv.request("key-press", **{"param-key": KEYS.get(step, step)})
                print(step, reply.get("status"))
                await asyncio.sleep(0.35)


asyncio.run(main(sys.argv[1:]))
