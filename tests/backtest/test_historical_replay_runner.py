from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.backtest import HistoricalReplayRequest, HistoricalReplayRunner
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.strategy_engine.contracts import StrategySignal


class ThresholdStrategy:
    required_candle_count = 3

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        if snapshot.last_price < 105.0:
            return None
        return StrategySignal(
            symbol=snapshot.symbol,
            direction="long",
            entry=snapshot.last_price,
            stop=snapshot.last_price - 5.0,
            target=snapshot.last_price + 10.0,
            reason="threshold",
            strategy_version="threshold-v1",
        )


class RecordingExecutionService:
    def __init__(self) -> None:
        self.executed_signals: list[ExecuteSignalRequest] = []

    async def execute_signal(self, *, signal: ExecuteSignalRequest) -> dict[str, object]:
        self.executed_signals.append(signal)
        return {"symbol": signal.symbol, "entry": signal.entry}


def build_candles(closes: list[float]) -> tuple[MarketCandle, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=index),
            high=close + 1.0,
            low=close - 1.0,
            close=close,
        )
        for index, close in enumerate(closes)
    )


@pytest.mark.asyncio
async def test_historical_replay_runner_replays_candle_range_into_step_results() -> None:
    execution_service = RecordingExecutionService()
    runner = HistoricalReplayRunner(
        strategy=ThresholdStrategy(),
        execution_service=execution_service,
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="1",
            candles=build_candles([100.0, 101.0, 102.0, 106.0, 108.0, 103.0]),
            start_step=3,
            end_step=6,
        )
    )

    assert [step.step_index for step in result.steps] == [3, 4, 5]
    assert [step.signal.reason if step.signal else None for step in result.steps] == [
        "threshold",
        "threshold",
        None,
    ]
    assert [step.execution for step in result.steps] == [
        {"symbol": "BTCUSDT", "entry": 106.0},
        {"symbol": "BTCUSDT", "entry": 108.0},
        None,
    ]


@pytest.mark.asyncio
async def test_historical_replay_runner_accepts_explicit_snapshot_sequence() -> None:
    execution_service = RecordingExecutionService()
    runner = HistoricalReplayRunner(
        strategy=ThresholdStrategy(),
        execution_service=execution_service,
    )
    snapshots = (
        MarketSnapshot(symbol="ETHUSDT", interval="5", candles=build_candles([100.0, 101.0, 102.0])),
        MarketSnapshot(symbol="ETHUSDT", interval="5", candles=build_candles([101.0, 103.0, 106.0])),
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="ETHUSDT",
            interval="5",
            snapshots=snapshots,
        )
    )

    assert len(result.steps) == 2
    assert result.steps[0].signal is None
    assert result.steps[1].signal is not None
    assert result.steps[1].execution == {"symbol": "ETHUSDT", "entry": 106.0}
