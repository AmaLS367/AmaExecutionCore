from __future__ import annotations

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from backend.admin import auth as admin_auth
from backend.config import settings


@pytest.fixture(autouse=True)
def _configure() -> None:
    settings.admin_jwt_secret = "test-secret-at-least-32-characters-ok"
    settings.trading_mode = "shadow"
    settings.shadow_equity = 10_000.0


def _access_token() -> str:
    return admin_auth.create_access_token("admin")


def _make_app() -> TestClient:
    from backend.admin.ws_logs import make_ws_router

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(make_ws_router())
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# WebSocket /admin/ws/logs — authentication
# ---------------------------------------------------------------------------


def test_ws_logs_rejects_missing_token() -> None:
    client = _make_app()
    with client.websocket_connect("/admin/ws/logs") as ws:
        data = ws.receive_json()
        assert data.get("error") is not None


def test_ws_logs_rejects_invalid_token() -> None:
    client = _make_app()
    with client.websocket_connect("/admin/ws/logs?token=not-a-valid-jwt") as ws:
        data = ws.receive_json()
        assert data.get("error") is not None


def test_ws_logs_accepts_valid_token_and_sends_connected_ack() -> None:
    token = _access_token()
    client = _make_app()
    with client.websocket_connect(f"/admin/ws/logs?token={token}") as ws:
        msg = ws.receive_json()
        assert msg.get("type") == "connected"


# ---------------------------------------------------------------------------
# _WebSocketHandler unit tests (no real WebSocket needed)
# ---------------------------------------------------------------------------


def test_ws_handler_delivers_log_to_subscriber() -> None:
    from backend.admin.ws_logs import _ws_handler

    async def run() -> str:
        q = _ws_handler.subscribe()
        try:
            logging.getLogger("backend.handler_unit_test").warning("handler-unit-msg-xyz")
            await asyncio.sleep(0)
            return q.get_nowait()
        finally:
            _ws_handler.unsubscribe(q)

    raw = asyncio.run(run())
    data = json.loads(raw)
    assert "handler-unit-msg-xyz" in data["message"]
    assert "timestamp" in data
    assert "level" in data
    assert "module" in data


def test_ws_handler_message_has_all_required_fields() -> None:
    from backend.admin.ws_logs import _ws_handler

    async def run() -> dict:
        q = _ws_handler.subscribe()
        try:
            logging.getLogger("backend.field_check").error("field-check-error")
            await asyncio.sleep(0)
            return json.loads(q.get_nowait())
        finally:
            _ws_handler.unsubscribe(q)

    msg = asyncio.run(run())
    assert set(msg.keys()) >= {"timestamp", "level", "module", "message"}
    assert msg["level"] == "ERROR"
