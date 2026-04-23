from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.backtest.replay_runner import (
    HistoricalReplayRequest,
    _build_candle_replay_steps,
    _build_report,
    _build_snapshot_replay_steps,
    _future_candles_for_step,
    _normalize_request,
)
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.contracts import StrategySignal


class _NoopStrategy:
    required_candle_count = 3

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        del snapshot
        return None


class _NoopExecutionService:
    async def execute_signal(self, *, signal: object) -> object:
        return {"signal": signal}


def _candles(count: int) -> tuple[MarketCandle, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=index),
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=10.0 + index,
        )
        for index in range(count)
    )


def _snapshots(count: int) -> tuple[MarketSnapshot, ...]:
    return tuple(
        MarketSnapshot(symbol="BTCUSDT", interval="5", candles=_candles(3))
        for _ in range(count)
    )


def test_normalize_request_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="symbol must not be empty"):
        _normalize_request(HistoricalReplayRequest(symbol=" ", interval="5", candles=_candles(3)))
    with pytest.raises(ValueError, match="interval must not be empty"):
        _normalize_request(HistoricalReplayRequest(symbol="BTCUSDT", interval=" ", candles=_candles(3)))
    with pytest.raises(ValueError, match="exactly one of candles or snapshots"):
        _normalize_request(
            HistoricalReplayRequest(symbol="BTCUSDT", interval="5", candles=_candles(3), snapshots=_snapshots(1)),
        )
    with pytest.raises(ValueError, match="start_step must be greater than or equal to zero"):
        _normalize_request(HistoricalReplayRequest(symbol="BTCUSDT", interval="5", candles=_candles(3), start_step=-1))
    with pytest.raises(ValueError, match="end_step must be greater than or equal to zero"):
        _normalize_request(HistoricalReplayRequest(symbol="BTCUSDT", interval="5", candles=_candles(3), end_step=-1))


def test_build_snapshot_replay_steps_validates_bounds() -> None:
    request = HistoricalReplayRequest(symbol="BTCUSDT", interval="5", snapshots=_snapshots(2), start_step=1, end_step=0)
    with pytest.raises(ValueError, match="end_step must be greater than or equal to start_step"):
        _build_snapshot_replay_steps(request)

    request = HistoricalReplayRequest(symbol="BTCUSDT", interval="5", snapshots=_snapshots(2), end_step=3)
    with pytest.raises(ValueError, match="exceeds available snapshots"):
        _build_snapshot_replay_steps(request)


def test_build_candle_replay_steps_validates_window_constraints() -> None:
    with pytest.raises(ValueError, match="do not satisfy the strategy candle requirement"):
        _build_candle_replay_steps(
            HistoricalReplayRequest(symbol="BTCUSDT", interval="5", candles=_candles(2)),
            required_candle_count=3,
        )
    with pytest.raises(ValueError, match="earlier than the first valid candle window"):
        _build_candle_replay_steps(
            HistoricalReplayRequest(symbol="BTCUSDT", interval="5", candles=_candles(5), start_step=1),
            required_candle_count=3,
        )
    with pytest.raises(ValueError, match="greater than or equal to start_step"):
        _build_candle_replay_steps(
            HistoricalReplayRequest(symbol="BTCUSDT", interval="5", candles=_candles(5), start_step=4, end_step=3),
            required_candle_count=3,
        )
    with pytest.raises(ValueError, match="exceeds available candles"):
        _build_candle_replay_steps(
            HistoricalReplayRequest(symbol="BTCUSDT", interval="5", candles=_candles(5), end_step=6),
            required_candle_count=3,
        )


def test_build_report_computes_profit_factor_and_drawdown() -> None:
    report = _build_report(
        [
            type("Step", (), {"execution": {"realized_pnl": "10", "slippage": "1"}})(),
            type("Step", (), {"execution": {"realized_pnl": "-4", "slippage": "2"}})(),
            type("Step", (), {"execution": {"realized_pnl": "-3", "slippage": "3"}})(),
        ],
    )

    assert report.metrics.closed_trades == 3
    assert report.metrics.winning_trades == 1
    assert report.metrics.losing_trades == 2
    assert report.metrics.expectancy == Decimal(1)
    assert report.metrics.win_rate == Decimal("0.3333333333333333333333333333")
    assert report.metrics.profit_factor == Decimal("1.428571428571428571428571429")
    assert report.metrics.max_drawdown == Decimal(7)
    assert report.slippage is not None
    assert report.slippage.minimum == Decimal(1)
    assert report.slippage.maximum == Decimal(3)


def test_future_candles_for_step_returns_remaining_tail() -> None:
    candles = _candles(5)
    request = HistoricalReplayRequest(symbol="BTCUSDT", interval="5", candles=candles)

    assert _future_candles_for_step(request, step_index=2) == candles[3:]
    assert _future_candles_for_step(HistoricalReplayRequest(symbol="BTCUSDT", interval="5", snapshots=_snapshots(2)), step_index=0) == ()
