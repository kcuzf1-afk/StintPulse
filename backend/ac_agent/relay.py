"""Live onboard video PC -> phone/tablet: WebRTC signaling plus frame fallback.

Primary path: WebRTC peer-to-peer inside the LAN. This relay only forwards the
small offer/answer/ICE messages.

Fallback path: if a direct WebRTC connection is impossible (e.g. the Windows
firewall blocks the browser's UDP on a "Public" network, or the phone browser
offers no usable candidates), viewers subscribe to JPEG frames that the PC
dashboard sends over this same WebSocket. Frames are forwarded to subscribed
viewers; a slow viewer gets the newest frame, never a growing queue.

Several PC dashboard tabs may be connected; the ACTIVE sender is the one that
most recently went online with a picture. A tab without a picture never pushes
out a tab that has one.
"""

import asyncio
import json
import secrets

MAX_MESSAGE = 64 * 1024
MAX_FRAME = 2 * 1024 * 1024
SENDER_TYPES = {"online", "offline", "offer", "ice"}
VIEWER_TYPES = {"request", "answer", "ice"}


class OnboardRelay:
    def __init__(self):
        self.senders = {}  # id -> {"ws": websocket, "live": bool}
        self.active = None  # id of the sender whose picture is shown
        self.viewers = {}  # id -> {"ws": websocket, "frames": bool, "busy": bool}

    @property
    def sender_live(self):
        return self.active is not None

    def status(self):
        return {
            "senders": len(self.senders),
            "sender_live": self.sender_live,
            "viewers": len(self.viewers),
            "frame_viewers": sum(1 for v in self.viewers.values() if v["frames"]),
        }

    async def _send(self, ws, message):
        try:
            await ws.send_text(json.dumps(message, separators=(",", ":")))
            return True
        except Exception:
            return False

    async def _to_viewers(self, message):
        for viewer in list(self.viewers.values()):
            await self._send(viewer["ws"], message)

    async def _counts(self):
        message = {
            "type": "viewers",
            "count": len(self.viewers),
            "frames": sum(1 for v in self.viewers.values() if v["frames"]),
        }
        for sender_id, sender in list(self.senders.items()):
            # Only the active sender needs to encode frames.
            await self._send(
                sender["ws"],
                {**message, "active": sender_id == self.active},
            )

    async def _activate(self, sender_id):
        """Make sender_id active (or pick another live one) and tell viewers."""
        if sender_id is None:
            live = [i for i, s in self.senders.items() if s["live"]]
            sender_id = live[-1] if live else None
        changed = sender_id != self.active
        self.active = sender_id
        if self.active is None:
            await self._to_viewers({"type": "sender-offline"})
        elif changed:
            await self._to_viewers({"type": "sender-online"})
        await self._counts()

    async def join(self, ws, role):
        client_id = secrets.token_hex(4)
        if role == "sender":
            self.senders[client_id] = {"ws": ws, "live": False}
            await self._send(ws, {"type": "welcome", "id": client_id})
        else:
            self.viewers[client_id] = {"ws": ws, "frames": False, "busy": False}
            await self._send(
                ws, {"type": "welcome", "id": client_id, "sender": self.sender_live}
            )
        await self._counts()
        return client_id

    async def leave(self, client_id, role):
        if role == "sender":
            self.senders.pop(client_id, None)
            if self.active == client_id:
                await self._activate(None)
            else:
                await self._counts()
        else:
            self.viewers.pop(client_id, None)
            if self.active in self.senders:
                await self._send(
                    self.senders[self.active]["ws"],
                    {"type": "viewer-left", "from": client_id},
                )
            await self._counts()

    async def handle(self, client_id, role, raw):
        if len(raw) > MAX_MESSAGE:
            return
        try:
            message = json.loads(raw)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if role == "sender":
            sender = self.senders.get(client_id)
            if not sender or kind not in SENDER_TYPES:
                return
            if kind == "online":
                sender["live"] = True
                if self.active != client_id:
                    await self._activate(client_id)
            elif kind == "offline":
                sender["live"] = False
                if self.active == client_id:
                    await self._activate(None)
            else:
                target = self.viewers.get(str(message.get("to")))
                if target:
                    await self._send(
                        target["ws"],
                        {
                            "type": kind,
                            "from": client_id,
                            "sdp": message.get("sdp"),
                            "candidate": message.get("candidate"),
                        },
                    )
            return
        viewer = self.viewers.get(client_id)
        if not viewer:
            return
        if kind == "frames":
            viewer["frames"] = bool(message.get("on"))
            await self._counts()
        elif kind in VIEWER_TYPES and self.active in self.senders:
            await self._send(
                self.senders[self.active]["ws"],
                {
                    "type": kind,
                    "from": client_id,
                    "sdp": message.get("sdp"),
                    "candidate": message.get("candidate"),
                },
            )

    async def frame(self, client_id, data):
        """JPEG frame from a sender: forward the newest frame, never queue."""
        if client_id != self.active or not data or len(data) > MAX_FRAME:
            return
        for viewer in list(self.viewers.values()):
            if viewer["frames"] and not viewer["busy"]:
                viewer["busy"] = True
                asyncio.ensure_future(self._push(viewer, data))

    async def _push(self, viewer, data):
        try:
            await asyncio.wait_for(viewer["ws"].send_bytes(data), timeout=2)
        except Exception:
            pass
        finally:
            viewer["busy"] = False

    async def close_all(self):
        """Used when the access code changes: everybody must re-authenticate."""
        sockets = [v["ws"] for v in self.viewers.values()] + [
            s["ws"] for s in self.senders.values()
        ]
        for ws in sockets:
            try:
                await ws.close(code=1008)
            except Exception:
                pass
