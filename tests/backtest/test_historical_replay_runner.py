from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
            open=close,
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
        cooldown_candles=0,
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="1",
            candles=build_candles([100.0, 101.0, 102.0, 106.0, 108.0, 103.0]),
            start_step=3,
            end_step=6,
        ),
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
        cooldown_candles=0,
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
        ),
    )

    assert len(result.steps) == 2
    assert result.steps[0].signal is None
    assert result.steps[1].signal is not None
    assert result.steps[1].execution == {"symbol": "ETHUSDT", "entry": 106.0}


class AlwaysSignalStrategy:
    required_candle_count = 1

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        return StrategySignal(
            symbol=snapshot.symbol,
            direction="long",
            entry=snapshot.last_price,
            stop=snapshot.last_price - 5.0,
            target=snapshot.last_price + 10.0,
            reason="always",
            strategy_version="always-v1",
        )


class PlannedExitExecutionService:
    def __init__(self, *, pnl_values: list[Decimal], close_step_offsets: list[int]) -> None:
        self._pnl_values = pnl_values
        self._close_step_offsets = close_step_offsets
        self._index = 0

    async def execute_replay_signal(
        self,
        *,
        signal: ExecuteSignalRequest,
        future_candles: tuple[MarketCandle, ...],
        step_index: int,
    ) -> dict[str, object]:
        del future_candles
        pnl_value = self._pnl_values[self._index]
        close_step_offset = self._close_step_offsets[self._index]
        self._index += 1
        return {
            "symbol": signal.symbol,
            "realized_pnl": pnl_value,
            "fees_paid": Decimal(0),
            "closed_at_step": step_index + close_step_offset,
            "entry_price": Decimal(str(signal.entry)),
        }


def build_snapshots_for_symbol(symbol: str, count: int) -> tuple[MarketSnapshot, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketSnapshot(
            symbol=symbol,
            interval="5",
            candles=(
                MarketCandle(
                    opened_at=opened_at + timedelta(minutes=index * 5),
                    open=100.0 + index,
                    high=101.0 + index,
                    low=99.0 + index,
                    close=100.0 + index,
                    volume=10.0 + index,
                ),
            ),
        )
        for index in range(count)
    )


def build_cross_day_snapshots(symbol: str, count: int, *, split_after: int) -> tuple[MarketSnapshot, ...]:
    opened_at = datetime(2024, 1, 1, 23, 45, tzinfo=UTC)
    snapshots: list[MarketSnapshot] = []
    for index in range(count):
        offset = index if index < split_after else index + 285
        candle = MarketCandle(
            opened_at=opened_at + timedelta(minutes=5 * offset),
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=10.0 + index,
        )
        snapshots.append(
            MarketSnapshot(symbol=symbol, interval="5", candles=(candle,)),
        )
    return tuple(snapshots)


@pytest.mark.asyncio
async def test_portfolio_state_blocks_second_entry_same_symbol() -> None:
    runner = HistoricalReplayRunner(
        strategy=AlwaysSignalStrategy(),
        execution_service=PlannedExitExecutionService(
            pnl_values=[Decimal(10)],
            close_step_offsets=[2],
        ),
        cooldown_candles=0,
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="5",
            snapshots=build_snapshots_for_symbol("BTCUSDT", 2),
        ),
    )

    assert result.steps[0].execution is not None
    assert result.steps[1].execution is None


@pytest.mark.asyncio
async def test_portfolio_state_cooldown() -> None:
    runner = HistoricalReplayRunner(
        strategy=AlwaysSignalStrategy(),
        execution_service=PlannedExitExecutionService(
            pnl_values=[Decimal(10), Decimal(10)],
            close_step_offsets=[0, 0],
        ),
        cooldown_candles=1,
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="5",
            snapshots=build_snapshots_for_symbol("BTCUSDT", 3),
        ),
    )

    assert result.steps[0].execution is not None
    assert result.steps[1].execution is None
    assert result.steps[2].execution is not None


@pytest.mark.asyncio
async def test_portfolio_state_daily_cap() -> None:
    runner = HistoricalReplayRunner(
        strategy=AlwaysSignalStrategy(),
        execution_service=PlannedExitExecutionService(
            pnl_values=[Decimal(10)],
            close_step_offsets=[0],
        ),
        cooldown_candles=0,
        max_trades_per_day=1,
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="5",
            snapshots=build_snapshots_for_symbol("BTCUSDT", 3),
        ),
    )

    assert result.steps[0].execution is not None
    assert result.steps[1].execution is None
    assert result.steps[2].execution is None


@pytest.mark.asyncio
async def test_circuit_breaker_halts_after_consecutive_losses() -> None:
    runner = HistoricalReplayRunner(
        strategy=AlwaysSignalStrategy(),
        execution_service=PlannedExitExecutionService(
            pnl_values=[Decimal(-10), Decimal(-10)],
            close_step_offsets=[0, 0],
        ),
        cooldown_candles=0,
        hard_pause_consecutive_losses=2,
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="5",
            snapshots=build_snapshots_for_symbol("BTCUSDT", 3),
        ),
    )

    assert result.steps[0].execution is not None
    assert result.steps[1].execution is not None
    assert result.steps[2].execution is None


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_new_trading_day() -> None:
    runner = HistoricalReplayRunner(
        strategy=AlwaysSignalStrategy(),
        execution_service=PlannedExitExecutionService(
            pnl_values=[Decimal(-10), Decimal(-10), Decimal(10)],
            close_step_offsets=[0, 0, 0],
        ),
        cooldown_candles=0,
        hard_pause_consecutive_losses=2,
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="5",
            snapshots=build_cross_day_snapshots("BTCUSDT", 4, split_after=3),
        ),
    )

    assert result.steps[0].execution is not None
    assert result.steps[1].execution is not None
    assert result.steps[2].execution is None
    assert result.steps[3].execution is not None
