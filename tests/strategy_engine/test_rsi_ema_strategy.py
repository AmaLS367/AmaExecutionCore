from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.rsi_ema_strategy import RSIEMAStrategy


def _build_snapshot(
    *,
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> MarketSnapshot:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    candle_highs = highs or [close + 1.0 for close in closes]
    candle_lows = lows or [close - 1.0 for close in closes]
    candles = tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=index),
            high=candle_highs[index],
            low=candle_lows[index],
            close=close,
            volume=100.0,
        )
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="15", candles=candles)


@pytest.mark.asyncio
async def test_rsi_ema_strategy_generates_long_signal() -> None:
    strategy = RSIEMAStrategy()
    snapshot = _build_snapshot(
        closes=[110.0] * 35 + [105.0, 103.0, 104.0, 110.0],
        lows=[109.0] * 35 + [104.0, 102.0, 103.0, 106.0],
    )

    signal = await strategy.generate_signal(snapshot)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry == 110.0
    assert signal.stop == 102.0
    assert signal.target == 122.0
    assert signal.reason == "rsi_ema_confluence"


@pytest.mark.asyncio
async def test_rsi_ema_strategy_generates_short_signal() -> None:
    strategy = RSIEMAStrategy()
    snapshot = _build_snapshot(
        closes=[100.0] * 35 + [105.0, 107.0, 106.0, 100.0],
        highs=[101.0] * 35 + [106.0, 108.0, 107.0, 104.0],
    )

    signal = await strategy.generate_signal(snapshot)

    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry == 100.0
    assert signal.stop == 108.0
    assert signal.target == 88.0
    assert signal.reason == "rsi_ema_confluence"


@pytest.mark.asyncio
async def test_rsi_ema_strategy_returns_none_without_threshold_reclaim() -> None:
    strategy = RSIEMAStrategy()
    snapshot = _build_snapshot(closes=[100.0] * strategy.required_candle_count)

    signal = await strategy.generate_signal(snapshot)

    assert signal is None


def test_rsi_ema_strategy_uses_warmup_formula_for_required_candle_count() -> None:
    strategy = RSIEMAStrategy(ema_period=20, rsi_period=14)

    assert strategy.required_candle_count == 39
