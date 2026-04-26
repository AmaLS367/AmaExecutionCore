from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.contracts import StrategySignal
from backend.strategy_engine.relative_strength_orchestrator import RelativeStrengthOrchestrator
from backend.strategy_engine.ts_momentum_strategy import TSMomentumStrategy


def _build_snapshot(symbol: str) -> MarketSnapshot:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    candles = tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=15 * index),
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=100.0,
        )
        for index in range(293)
    )
    return MarketSnapshot(symbol=symbol, interval="15", candles=candles)


@pytest.mark.asyncio
async def test_picks_highest_scoring_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    btc = TSMomentumStrategy()
    eth = TSMomentumStrategy()
    sol = TSMomentumStrategy()
    orchestrator = RelativeStrengthOrchestrator(
        strategies={"BTCUSDT": btc, "ETHUSDT": eth, "SOLUSDT": sol},
    )
    snapshots = {
        "BTCUSDT": _build_snapshot("BTCUSDT"),
        "ETHUSDT": _build_snapshot("ETHUSDT"),
        "SOLUSDT": _build_snapshot("SOLUSDT"),
    }
    monkeypatch.setattr(
        RelativeStrengthOrchestrator,
        "_btc_regime_allows",
        lambda _self, _snapshot: True,
    )
    monkeypatch.setattr(btc, "compute_momentum_score", lambda _snapshot: 0.5)
    monkeypatch.setattr(eth, "compute_momentum_score", lambda _snapshot: 1.2)
    monkeypatch.setattr(sol, "compute_momentum_score", lambda _snapshot: 0.8)

    async def _eth_signal(_snapshot: MarketSnapshot) -> StrategySignal:
        return StrategySignal(symbol="ETHUSDT", direction="long", entry=1.0, stop=0.5, target=2.0)

    monkeypatch.setattr(eth, "generate_signal", _eth_signal)

    signal = await orchestrator.select_signal(snapshots, btc_snapshot=snapshots["BTCUSDT"])

    assert signal is not None
    assert signal.symbol == "ETHUSDT"


@pytest.mark.asyncio
async def test_returns_none_when_btc_regime_bearish(monkeypatch: pytest.MonkeyPatch) -> None:
    btc = TSMomentumStrategy()
    eth = TSMomentumStrategy()
    orchestrator = RelativeStrengthOrchestrator(
        strategies={"BTCUSDT": btc, "ETHUSDT": eth},
    )
    snapshots = {
        "BTCUSDT": _build_snapshot("BTCUSDT"),
        "ETHUSDT": _build_snapshot("ETHUSDT"),
    }
    monkeypatch.setattr(
        RelativeStrengthOrchestrator,
        "_btc_regime_allows",
        lambda _self, _snapshot: False,
    )
    monkeypatch.setattr(eth, "compute_momentum_score", lambda _snapshot: 1.2)

    signal = await orchestrator.select_signal(snapshots, btc_snapshot=snapshots["BTCUSDT"])

    assert signal is None


@pytest.mark.asyncio
async def test_returns_none_when_no_positive_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    btc = TSMomentumStrategy()
    eth = TSMomentumStrategy()
    sol = TSMomentumStrategy()
    orchestrator = RelativeStrengthOrchestrator(
        strategies={"BTCUSDT": btc, "ETHUSDT": eth, "SOLUSDT": sol},
    )
    snapshots = {
        "BTCUSDT": _build_snapshot("BTCUSDT"),
        "ETHUSDT": _build_snapshot("ETHUSDT"),
        "SOLUSDT": _build_snapshot("SOLUSDT"),
    }
    monkeypatch.setattr(
        RelativeStrengthOrchestrator,
        "_btc_regime_allows",
        lambda _self, _snapshot: True,
    )
    monkeypatch.setattr(btc, "compute_momentum_score", lambda _snapshot: None)
    monkeypatch.setattr(eth, "compute_momentum_score", lambda _snapshot: -0.2)
    monkeypatch.setattr(sol, "compute_momentum_score", lambda _snapshot: 0.0)

    signal = await orchestrator.select_signal(snapshots, btc_snapshot=snapshots["BTCUSDT"])

    assert signal is None
