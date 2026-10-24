"""Print the TV's BrightScript debug console (port 8085) for N seconds.

    python tools/console.py [seconds]

Shows the launcher's print output, compile errors and, if it crashed, the
debugger with a stack trace (a frozen launcher is usually paused there).
Only one connection is allowed at a time.
"""
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

tv = json.loads((ROOT / "config.json").read_text())["tv"]
end = time.time() + (float(sys.argv[1]) if len(sys.argv) > 1 else 30)
conn = socket.create_connection((tv, 8085), timeout=5)
conn.settimeout(1)
while time.time() < end:
    try:
        data = conn.recv(4096)
        if not data:
            break
        sys.stdout.write(data.decode("utf-8", "replace"))
        sys.stdout.flush()
    except socket.timeout:
        pass
