from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.backtest import HistoricalReplayRequest, HistoricalReplayRunner
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.contracts import StrategySignal


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


class OutcomeExecutionService:
    def __init__(self, outcomes: list[dict[str, Decimal]]) -> None:
        self._outcomes = outcomes
        self._index = 0

    async def execute_signal(self, *, signal: object) -> dict[str, Decimal]:
        outcome = self._outcomes[self._index]
        self._index += 1
        return outcome


def build_snapshots(count: int) -> tuple[MarketSnapshot, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketSnapshot(
            symbol="BTCUSDT",
            interval="1",
            candles=(
                MarketCandle(
                    opened_at=opened_at + timedelta(minutes=index),
                    high=100.0 + index,
                    low=99.0 + index,
                    close=100.0 + index,
                ),
            ),
        )
        for index in range(count)
    )


@pytest.mark.asyncio
async def test_historical_replay_runner_returns_machine_readable_metrics_report() -> None:
    runner = HistoricalReplayRunner(
        strategy=AlwaysSignalStrategy(),
        execution_service=OutcomeExecutionService(
            [
                {"realized_pnl": Decimal(10), "slippage": Decimal("0.1")},
                {"realized_pnl": Decimal(-5), "slippage": Decimal("0.2")},
                {"realized_pnl": Decimal(-15), "slippage": Decimal("0.3")},
                {"realized_pnl": Decimal(20), "slippage": Decimal("0.4")},
            ],
        ),
        cooldown_candles=0,
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="1",
            snapshots=build_snapshots(4),
        ),
    )

    assert result.report.metrics.closed_trades == 4
    assert result.report.metrics.winning_trades == 2
    assert result.report.metrics.losing_trades == 2
    assert result.report.metrics.expectancy == Decimal("2.5")
    assert result.report.metrics.win_rate == Decimal("0.5")
    assert result.report.metrics.profit_factor == Decimal("1.5")
    assert result.report.metrics.max_drawdown == Decimal(20)
    assert result.report.slippage is not None
    assert result.report.slippage.count == 4
    assert result.report.slippage.average == Decimal("0.25")
    assert result.report.slippage.minimum == Decimal("0.1")
    assert result.report.slippage.maximum == Decimal("0.4")


@pytest.mark.asyncio
async def test_historical_replay_runner_does_not_invent_metrics_without_trade_outcomes() -> None:
    runner = HistoricalReplayRunner(
        strategy=AlwaysSignalStrategy(),
        execution_service=OutcomeExecutionService([{}, {}]),
        cooldown_candles=0,
    )

    result = await runner.replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="1",
            snapshots=build_snapshots(2),
        ),
    )

    assert result.report.metrics.closed_trades == 0
    assert result.report.metrics.expectancy is None
    assert result.report.metrics.win_rate is None
    assert result.report.metrics.profit_factor is None
    assert result.report.metrics.max_drawdown is None
    assert result.report.slippage is None
