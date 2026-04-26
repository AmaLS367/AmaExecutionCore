from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import NamedTuple

from fastapi import APIRouter, WebSocket

from backend.admin.auth import decode_token


class _Subscriber(NamedTuple):
    queue: asyncio.Queue[str]
    loop: asyncio.AbstractEventLoop


class _WebSocketHandler(logging.Handler):
    """Forwards log records to connected WebSocket clients (thread-safe)."""

    def __init__(self) -> None:
        super().__init__()
        self._subscribers: set[_Subscriber] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        sub = _Subscriber(queue=q, loop=asyncio.get_running_loop())
        self._subscribers.add(sub)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subscribers = {s for s in self._subscribers if s.queue is not q}

    def emit(self, record: logging.LogRecord) -> None:
        payload = json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "module": record.name,
                "message": self.format(record),
            },
        )
        for sub in list(self._subscribers):
            try:
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, payload)
            except (RuntimeError, asyncio.QueueFull):
                pass


_ws_handler = _WebSocketHandler()
_ws_handler.setLevel(logging.DEBUG)

_root_logger = logging.getLogger("backend")
_root_logger.addHandler(_ws_handler)


def make_ws_router() -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin-ws"])

    @router.websocket("/ws/logs")
    async def ws_logs(websocket: WebSocket) -> None:
        await websocket.accept()

        try:
            data = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
            token = data.get("token")
        except Exception:
            await websocket.close(code=1008)
            return

        if not token:
            await websocket.send_json({"error": "token required"})
            await websocket.close(code=1008)
            return

        try:
            decode_token(token, expected_type="access")
        except Exception:
            await websocket.send_json({"error": "invalid or expired token"})
            await websocket.close(code=1008)
            return

        await websocket.send_json({"type": "connected"})

        queue = _ws_handler.subscribe()
        try:
            while True:
                payload = await queue.get()
                await websocket.send_text(payload)
        except Exception:
            pass
        finally:
            _ws_handler.unsubscribe(queue)

    return router
