from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.backtest.simulation_execution_service import SimulationExecutionService
from backend.market_data.contracts import MarketCandle
from backend.signal_execution.schemas import ExecuteSignalRequest


def _build_candles(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    opens: list[float] | None = None,
) -> tuple[MarketCandle, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=index),
            open=opens[index] if opens is not None else closes[index],
            high=highs[index],
            low=lows[index],
            close=closes[index],
            volume=100.0,
        )
        for index in range(len(closes))
    )


@pytest.mark.asyncio
async def test_simulation_execution_service_returns_take_profit_result() -> None:
    service = SimulationExecutionService(max_hold_candles=5, risk_amount_usd=100.0, fee_rate_per_side=0.0)

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

    assert result.realized_pnl == Decimal(200)
    assert result.exit_reason == "tp_hit"
    assert result.hold_candles == 2


@pytest.mark.asyncio
async def test_simulation_execution_service_returns_long_stop_loss() -> None:
    service = SimulationExecutionService(max_hold_candles=5, risk_amount_usd=100.0, fee_rate_per_side=0.0)

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
    service = SimulationExecutionService(max_hold_candles=5, risk_amount_usd=100.0, fee_rate_per_side=0.0)

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

    assert result.realized_pnl == Decimal(200)
    assert result.exit_reason == "tp_hit"
    assert result.hold_candles == 2


@pytest.mark.asyncio
async def test_simulation_execution_service_returns_short_stop_loss() -> None:
    service = SimulationExecutionService(max_hold_candles=5, risk_amount_usd=100.0, fee_rate_per_side=0.0)

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
    service = SimulationExecutionService(max_hold_candles=2, risk_amount_usd=100.0, fee_rate_per_side=0.0)

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
    service = SimulationExecutionService(max_hold_candles=2, risk_amount_usd=100.0, fee_rate_per_side=0.0)

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
    assert result.realized_pnl == Decimal(0)


@pytest.mark.asyncio
async def test_simulation_execution_service_returns_zero_pnl_when_risk_is_zero() -> None:
    service = SimulationExecutionService(max_hold_candles=2, risk_amount_usd=100.0, fee_rate_per_side=0.0)

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
    assert result.realized_pnl == Decimal(0)


@pytest.mark.asyncio
async def test_simulation_execution_service_calculates_fees() -> None:
    service = SimulationExecutionService(
        max_hold_candles=5,
        risk_amount_usd=100.0,
        fee_rate_per_side=0.001,
    )

    result = await service.execute_replay_signal(
        signal=ExecuteSignalRequest(
            symbol="BTCUSDT",
            direction="long",
            entry=100.0,
            stop=95.0,
            target=110.0,
        ),
        future_candles=_build_candles([102.0, 111.0], [99.0, 100.0], [101.0, 109.0]),
        step_index=0,
    )

    assert result.fees_paid == Decimal("4.2")


@pytest.mark.asyncio
async def test_realistic_costs_reduce_pnl() -> None:
    legacy_service = SimulationExecutionService(
        max_hold_candles=5,
        risk_amount_usd=100.0,
        fee_rate_per_side=0.001,
    )
    realistic_service = SimulationExecutionService(
        max_hold_candles=5,
        risk_amount_usd=100.0,
        maker_fill_probability=0.0,
        spread_bps=Decimal(2),
        slippage_bps=Decimal(1),
        one_bar_execution_delay=False,
    )
    signal = ExecuteSignalRequest(
        symbol="BTCUSDT",
        direction="long",
        entry=100.0,
        stop=95.0,
        target=110.0,
    )
    candles = _build_candles([102.0, 111.0], [99.0, 100.0], [101.0, 109.0], opens=[100.0, 101.0])

    legacy_result = await legacy_service.execute_replay_signal(
        signal=signal,
        future_candles=candles,
        step_index=0,
    )
    realistic_result = await realistic_service.execute_replay_signal(
        signal=signal,
        future_candles=candles,
        step_index=0,
    )

    legacy_net_pnl = legacy_result.realized_pnl - legacy_result.fees_paid
    realistic_net_pnl = realistic_result.realized_pnl - realistic_result.fees_paid

    assert realistic_net_pnl < legacy_net_pnl


@pytest.mark.asyncio
async def test_one_bar_delay_uses_next_bar_open() -> None:
    service = SimulationExecutionService(
        max_hold_candles=5,
        risk_amount_usd=100.0,
        maker_fill_probability=1.0,
        spread_bps=Decimal(0),
        slippage_bps=Decimal(0),
        one_bar_execution_delay=True,
    )

    result = await service.execute_replay_signal(
        signal=ExecuteSignalRequest(
            symbol="BTCUSDT",
            direction="long",
            entry=100.0,
            stop=95.0,
            target=110.0,
        ),
        future_candles=_build_candles(
            [111.0, 112.0],
            [99.0, 100.0],
            [110.0, 111.0],
            opens=[101.5, 110.5],
        ),
        step_index=3,
    )

    assert result.entry_price == Decimal("101.5")
