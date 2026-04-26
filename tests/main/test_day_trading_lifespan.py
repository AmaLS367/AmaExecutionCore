from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.main import create_app
from backend.strategy_engine.contracts import StrategySignal


class NoopRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}


@dataclass(slots=True)
class _DummyStrategy:
    required_candle_count: int = 39

    async def generate_signal(self, snapshot: object) -> StrategySignal | None:
        del snapshot
        return None


class _RecordingSignalLoopRunner:
    last_init: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_init = kwargs

    async def run_forever(self) -> None:
        return None

    def stop(self) -> None:
        return None


def test_create_app_lifespan_builds_configured_day_trading_strategy(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.config import settings

    strategy_calls: list[tuple[str, float]] = []

    def _fake_build_day_trading_strategy(*, strategy_name: str, min_rrr: float) -> _DummyStrategy:
        strategy_calls.append((strategy_name, min_rrr))
        return _DummyStrategy()

    settings.signal_loop_enabled = True
    settings.signal_loop_symbols = ["BTCUSDT"]
    settings.signal_loop_interval = "15"
    settings.signal_loop_strategy = "rsi_ema"
    settings.min_rrr = 2.0
    settings.scalping_enabled = False
    settings.scalping_symbols = []

    async def _async_noop(*args: Any, **kwargs: Any) -> None:
        pass

    def _noop(*args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr("backend.main.ws_listener.start", lambda: None)
    monkeypatch.setattr("backend.main.ws_listener.stop", lambda: None)
    monkeypatch.setattr("backend.main.ExchangeSyncEngine.start_reconciliation_worker", _noop)
    monkeypatch.setattr("backend.main.ExchangeSyncEngine.stop_reconciliation_worker", _async_noop)

    monkeypatch.setattr("backend.main.build_day_trading_strategy", _fake_build_day_trading_strategy)
    monkeypatch.setattr("backend.main.SignalLoopRunner", _RecordingSignalLoopRunner)

    app = create_app(session_factory=sqlite_session_factory, rest_client=NoopRestClient())

    with TestClient(app):
        assert strategy_calls == [("rsi_ema", 2.0)]
        assert _RecordingSignalLoopRunner.last_init is not None
        assert _RecordingSignalLoopRunner.last_init["interval"] == "15"
