"""Minimal client for the Roku ECP-2 WebSocket (ws://<tv>:8060/ecp-session).

The ECP-2 socket is what Roku's mobile app uses. Unlike plain HTTP ECP, it still
answers query-apps / query-icon when the TV's "Control by mobile apps" setting is
Limited. The auth scheme is reverse-engineered: SHA1(challenge + key), where the
key is a fixed GUID from the mobile app run through a nibble transform.
"""
import asyncio
import base64
import hashlib
import json
import xml.etree.ElementTree as ET

import websockets

_APP_KEY = "95E610D0-7C29-44EF-FB0F-97F1FCE4C297"


def _transform_key(key, shift=9):
    out = []
    for c in key:
        if "0" <= c <= "9":
            v = ord(c) - 48
        elif "A" <= c <= "F":
            v = ord(c) - 55
        else:
            out.append(c)
            continue
        v = (15 - v + shift) & 15
        out.append(chr(v + 48) if v < 10 else chr(v + 55))
    return "".join(out)


AUTH_KEY = _transform_key(_APP_KEY)


class EcpError(RuntimeError):
    pass


class EcpSession:
    """Authenticated ECP-2 connection. Use as `async with EcpSession(ip) as tv:`."""

    def __init__(self, host, timeout=5):
        self.url = f"ws://{host}:8060/ecp-session"
        self.timeout = timeout
        self._ws = None
        self._next_id = 0

    async def __aenter__(self):
        self._ws = await websockets.connect(self.url, subprotocols=["ecp-2"],
                                            open_timeout=self.timeout, max_size=None)
        hello = json.loads(await asyncio.wait_for(self._ws.recv(), self.timeout))
        digest = hashlib.sha1((hello["param-challenge"] + AUTH_KEY).encode()).digest()
        reply = await self.request("authenticate",
                                   **{"param-response": base64.b64encode(digest).decode()})
        if reply.get("status") != "200":
            raise EcpError(f"authentication rejected: {reply}")
        return self

    async def __aexit__(self, *exc):
        await self._ws.close()

    async def request(self, name, **params):
        """Send one request and return its response, skipping unrelated notifications."""
        self._next_id += 1
        rid = str(self._next_id)
        await self._ws.send(json.dumps({"request": name, "request-id": rid, **params}))
        while True:
            msg = json.loads(await asyncio.wait_for(self._ws.recv(), self.timeout))
            if msg.get("response-id") == rid:
                return msg

    async def content(self, name, **params):
        """Request and return the decoded content-data bytes, raising on non-200."""
        msg = await self.request(name, **params)
        if msg.get("status") != "200":
            raise EcpError(f"{name} failed: {msg.get('status')} {msg.get('status-msg')}")
        return base64.b64decode(msg.get("content-data", ""))

    async def apps(self):
        """Installed apps and TV inputs as [{'id', 'name', 'type'}]."""
        root = ET.fromstring(await self.content("query-apps"))
        return [{"id": a.get("id"), "type": a.get("type"),
                 "name": (a.text or "").replace("\xa0", " ").strip()}
                for a in root.iter("app")]

    async def icon(self, app_id):
        return await self.content("query-icon", **{"param-channel-id": app_id})

    async def active_app(self):
        app = ET.fromstring(await self.content("query-active-app")).find("app")
        return {"id": app.get("id"), "type": app.get("type"), "name": app.text}

    async def launch(self, app_id, **params):
        return await self.request("launch", **{"param-channel-id": app_id, **params})

    async def events(self, names):
        """Subscribe to the named events and yield each notification as it arrives.
        Don't call request() on this session while iterating."""
        reply = await self.request("request-events",
                                   **{"param-events": ",".join("+" + n for n in names)})
        if reply.get("status") not in ("200", "202"):
            raise EcpError(f"request-events failed: {reply}")
        async for raw in self._ws:
            msg = json.loads(raw)
            if "notify" in msg:
                yield msg
