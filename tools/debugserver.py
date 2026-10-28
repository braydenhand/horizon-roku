"""Send commands to the TV's developer debug server (port 8080) and print the replies.

    python tools/debugserver.py "sgnodes roots" "loaded_textures"

Useful commands: sgnodes all|roots|<id>, loaded_textures, r2d2_bitmaps,
chanperf, fps_display, help. Works for whatever is running, including (in
some Roku OS versions) a screensaver.
"""
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tv = json.loads((ROOT / "config.json").read_text())["tv"]
conn = socket.create_connection((tv, 8080), timeout=5)
conn.settimeout(1.5)


def drain():
    out = b""
    while True:
        try:
            chunk = conn.recv(65536)
            if not chunk:
                break
            out += chunk
        except socket.timeout:
            break
    return out.decode("utf-8", "replace")


print(drain(), end="")
for command in sys.argv[1:] or ["help"]:
    conn.sendall((command + "\n").encode())
    time.sleep(0.5)
    print(f"### {command}\n{drain()}")
