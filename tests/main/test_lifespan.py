from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.main import create_app


class NoopRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}


def test_create_app_lifespan_rejects_overlapping_signal_loop_and_scalping_symbols(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.config import settings

    settings.signal_loop_enabled = True
    settings.signal_loop_symbols = ["BTCUSDT"]
    settings.signal_loop_interval = "15"
    settings.scalping_enabled = True
    settings.scalping_symbols = ["BTCUSDT"]
    settings.scalping_interval = "5"

    app = create_app(session_factory=sqlite_session_factory, rest_client=NoopRestClient())

    with pytest.raises(RuntimeError, match="overlap"):
        with TestClient(app):
            pass
