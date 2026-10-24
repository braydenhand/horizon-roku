"""Send ECP-2 requests to the TV and print the decoded answers.

    python tools/ws_query.py query-active-app query-apps query-media-player
    python tools/ws_query.py query-device-info

Works even when "Control by mobile apps" is Limited (plain HTTP ECP then
refuses most queries). The TV address comes from config.json.
"""
import asyncio
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from roku_ecp import EcpSession  # noqa: E402


async def main(requests):
    tv = json.loads((ROOT / "config.json").read_text())["tv"]
    async with EcpSession(tv) as session:
        for name in requests:
            reply = await session.request(name)
            print(f"=== {name}: {reply.get('status')} {reply.get('status-msg')}")
            if "content-data" in reply:
                print(base64.b64decode(reply["content-data"]).decode(errors="replace"))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["query-active-app"]))
