from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from backend.backtest import HistoricalReplayRequest, HistoricalReplayRunner
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.strategy_engine.contracts import StrategySignal


class AlwaysSignalStrategy:
    required_candle_count = 2

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        return StrategySignal(
            symbol=snapshot.symbol,
            direction="long",
            entry=snapshot.last_price,
            stop=snapshot.last_price - 2.0,
            target=snapshot.last_price + 4.0,
            reason="always",
        )


@dataclass(slots=True, frozen=True)
class ContextResult:
    future_count: int
    step_index: int


class ContextAwareExecutionService:
    def __init__(self) -> None:
        self.calls: list[tuple[ExecuteSignalRequest, tuple[MarketCandle, ...], int]] = []

    async def execute_replay_signal(
        self,
        *,
        signal: ExecuteSignalRequest,
        future_candles: tuple[MarketCandle, ...],
        step_index: int,
    ) -> ContextResult:
        self.calls.append((signal, future_candles, step_index))
        return ContextResult(future_count=len(future_candles), step_index=step_index)


def _build_candles(closes: list[float]) -> tuple[MarketCandle, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=index),
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=100.0,
        )
        for index, close in enumerate(closes)
    )


@pytest.mark.asyncio
async def test_historical_replay_runner_passes_future_candles_to_context_aware_execution_service() -> None:
    execution_service = ContextAwareExecutionService()
    runner: HistoricalReplayRunner[ContextResult] = HistoricalReplayRunner(
        strategy=AlwaysSignalStrategy(),
        execution_service=execution_service,  # type: ignore[arg-type]
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="5",
            candles=_build_candles([100.0, 101.0, 102.0, 103.0]),
        )
    )

    assert [step.execution for step in result.steps] == [
        ContextResult(future_count=2, step_index=1),
        ContextResult(future_count=1, step_index=2),
        ContextResult(future_count=0, step_index=3),
    ]
