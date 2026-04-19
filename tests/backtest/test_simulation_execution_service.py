from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.backtest.simulation_execution_service import SimulationExecutionService
from backend.market_data.contracts import MarketCandle
from backend.signal_execution.schemas import ExecuteSignalRequest


def _build_candles(highs: list[float], lows: list[float], closes: list[float]) -> tuple[MarketCandle, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=index),
            high=highs[index],
            low=lows[index],
            close=closes[index],
            volume=100.0,
        )
        for index in range(len(closes))
    )


@pytest.mark.asyncio
async def test_simulation_execution_service_returns_take_profit_result() -> None:
    service = SimulationExecutionService(max_hold_candles=5, risk_amount_usd=100.0)

    result = await service.execute_replay_signal(
        signal=ExecuteSignalRequest(
            symbol="BTCUSDT",
            direction="long",
            entry=100.0,
            stop=95.0,
            target=110.0,
        ),
        future_candles=_build_candles([102.0, 111.0], [99.0, 100.0], [101.0, 109.0]),
        step_index=3,
    )

    assert result.realized_pnl == Decimal("200")
    assert result.exit_reason == "tp_hit"
    assert result.hold_candles == 2


@pytest.mark.asyncio
async def test_simulation_execution_service_returns_long_stop_loss() -> None:
    service = SimulationExecutionService(max_hold_candles=5, risk_amount_usd=100.0)

    result = await service.execute_replay_signal(
        signal=ExecuteSignalRequest(
            symbol="BTCUSDT",
            direction="long",
            entry=100.0,
            stop=95.0,
            target=110.0,
        ),
        future_candles=_build_candles([101.0], [94.0], [96.0]),
        step_index=1,
    )

    assert result.realized_pnl == Decimal("-100.0")
    assert result.exit_reason == "sl_hit"
    assert result.hold_candles == 1


@pytest.mark.asyncio
async def test_simulation_execution_service_returns_short_take_profit() -> None:
    service = SimulationExecutionService(max_hold_candles=5, risk_amount_usd=100.0)

    result = await service.execute_replay_signal(
        signal=ExecuteSignalRequest(
            symbol="BTCUSDT",
            direction="short",
            entry=100.0,
            stop=105.0,
            target=90.0,
        ),
        future_candles=_build_candles([101.0, 102.0], [95.0, 89.0], [98.0, 91.0]),
        step_index=2,
    )

    assert result.realized_pnl == Decimal("200")
    assert result.exit_reason == "tp_hit"
    assert result.hold_candles == 2


@pytest.mark.asyncio
async def test_simulation_execution_service_returns_short_stop_loss() -> None:
    service = SimulationExecutionService(max_hold_candles=5, risk_amount_usd=100.0)

    result = await service.execute_replay_signal(
        signal=ExecuteSignalRequest(
            symbol="BTCUSDT",
            direction="short",
            entry=100.0,
            stop=105.0,
            target=90.0,
        ),
        future_candles=_build_candles([106.0], [99.0], [104.0]),
        step_index=4,
    )

    assert result.realized_pnl == Decimal("-100.0")
    assert result.exit_reason == "sl_hit"


@pytest.mark.asyncio
async def test_simulation_execution_service_returns_timeout_when_levels_are_not_hit() -> None:
    service = SimulationExecutionService(max_hold_candles=2, risk_amount_usd=100.0)

    result = await service.execute_replay_signal(
        signal=ExecuteSignalRequest(
            symbol="BTCUSDT",
            direction="short",
            entry=100.0,
            stop=105.0,
            target=90.0,
        ),
        future_candles=_build_candles([101.0, 102.0], [98.0, 97.0], [99.0, 98.0]),
        step_index=7,
    )

    assert result.exit_reason == "timeout"
    assert result.hold_candles == 2
    assert result.realized_pnl == Decimal("40.0")


@pytest.mark.asyncio
async def test_simulation_execution_service_handles_empty_future_candles() -> None:
    service = SimulationExecutionService(max_hold_candles=2, risk_amount_usd=100.0)

    result = await service.execute_replay_signal(
        signal=ExecuteSignalRequest(
            symbol="BTCUSDT",
            direction="long",
            entry=100.0,
            stop=95.0,
            target=110.0,
        ),
        future_candles=(),
        step_index=0,
    )

    assert result.exit_reason == "timeout"
    assert result.hold_candles == 0
    assert result.realized_pnl == Decimal("0")


@pytest.mark.asyncio
async def test_simulation_execution_service_returns_zero_pnl_when_risk_is_zero() -> None:
    service = SimulationExecutionService(max_hold_candles=2, risk_amount_usd=100.0)

    result = await service.execute_replay_signal(
        signal=ExecuteSignalRequest(
            symbol="BTCUSDT",
            direction="long",
            entry=100.0,
            stop=100.0,
            target=110.0,
        ),
        future_candles=_build_candles([101.0], [100.1], [105.0]),
        step_index=0,
    )

    assert result.exit_reason == "timeout"
    assert result.realized_pnl == Decimal("0")
