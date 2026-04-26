from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import NamedTuple, Protocol, cast

import jwt
from fastapi import APIRouter, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.admin.auth import decode_token
from backend.admin.models import AdminUser

_REVALIDATE_INTERVAL_SECONDS = 30.0


class _RedisLike(Protocol):
    async def exists(self, name: str) -> int: ...


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


async def _validate_ws_access(websocket: WebSocket, token: str) -> None:
    payload = decode_token(token, expected_type="access")
    jti = str(payload.get("jti", ""))
    redis_client = cast("_RedisLike", websocket.app.state.redis)
    if jti and await redis_client.exists(f"bl:{jti}"):
        raise jwt.PyJWTError("Token revoked")

    username = str(payload.get("sub", ""))
    factory = getattr(websocket.app.state, "session_factory", None)
    if factory is None:
        return

    session_factory = cast("async_sessionmaker[AsyncSession]", factory)
    async with session_factory() as session:
        is_active = await session.scalar(
            select(AdminUser.is_active).where(AdminUser.username == username),
        )
    if is_active is not True:
        raise jwt.PyJWTError("Admin user inactive")


async def _close_if_access_invalid(websocket: WebSocket, token: str) -> bool:
    try:
        await _validate_ws_access(websocket, token)
    except Exception:
        await websocket.close(code=1008)
        return True
    return False


async def _receive_ws_token(websocket: WebSocket) -> str | None:
    try:
        data: object = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
    except Exception:
        await websocket.close(code=1008)
        return None

    raw_token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(raw_token, str) or not raw_token:
        await websocket.send_json({"error": "token required"})
        await websocket.close(code=1008)
        return None
    return raw_token


def make_ws_router() -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin-ws"])

    @router.websocket("/ws/logs")
    async def ws_logs(websocket: WebSocket) -> None:
        await websocket.accept()

        token = await _receive_ws_token(websocket)
        if token is None:
            return

        try:
            await _validate_ws_access(websocket, token)
        except Exception:
            await websocket.send_json({"error": "invalid or expired token"})
            await websocket.close(code=1008)
            return

        await websocket.send_json({"type": "connected"})

        queue = _ws_handler.subscribe()
        try:
            next_validation_at = time.monotonic() + _REVALIDATE_INTERVAL_SECONDS
            while True:
                timeout = max(0.0, next_validation_at - time.monotonic())
                try:
                    log_text = await asyncio.wait_for(queue.get(), timeout=timeout)
                except TimeoutError:
                    if await _close_if_access_invalid(websocket, token):
                        return
                    next_validation_at = time.monotonic() + _REVALIDATE_INTERVAL_SECONDS
                    continue

                if time.monotonic() >= next_validation_at:
                    if await _close_if_access_invalid(websocket, token):
                        return
                    next_validation_at = time.monotonic() + _REVALIDATE_INTERVAL_SECONDS
                await websocket.send_text(log_text)
        except Exception:
            pass
        finally:
            _ws_handler.unsubscribe(queue)

    return router
