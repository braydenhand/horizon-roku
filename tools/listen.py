"""Print the TV's ECP-2 events as they happen (app launches/exits, power).

    python tools/listen.py [seconds]

Reconnects if the TV drops the connection. Handy for seeing what the watcher
sees; the TV address comes from config.json.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from roku_ecp import EcpSession  # noqa: E402

EVENTS = ["power-mode-changed", "plugin-ui-run", "plugin-ui-run-script", "plugin-ui-exit",
          "media-player-state-changed", "screensaver-show", "screensaver-hide",
          "tv-input-changed", "tv-channel-changed", "sound-volume-changed"]


def log(text):
    print(time.strftime("%H:%M:%S"), text, flush=True)


async def main(seconds):
    tv = json.loads((ROOT / "config.json").read_text())["tv"]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            async with EcpSession(tv) as session:
                log("connected")
                async def read():
                    async for msg in session.events(EVENTS):
                        log(json.dumps(msg))
                await asyncio.wait_for(read(), deadline - time.monotonic())
        except (asyncio.TimeoutError, TimeoutError):
            break
        except Exception as e:
            log(f"connection lost ({type(e).__name__}: {e})")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 120))
