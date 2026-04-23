from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.ema_crossover import EMACrossoverStrategy


def build_snapshot(*, closes: list[float], last_high: float, last_low: float) -> MarketSnapshot:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    candles = tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=index),
            high=close + 1 if index < len(closes) - 1 else last_high,
            low=close - 1 if index < len(closes) - 1 else last_low,
            close=close,
        )
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="1", candles=candles)


@pytest.mark.asyncio
async def test_ema_crossover_generates_bullish_signal() -> None:
    strategy = EMACrossoverStrategy()
    snapshot = build_snapshot(closes=[100.0] * 21 + [130.0], last_high=132.0, last_low=120.0)

    signal = await strategy.generate_signal(snapshot)

    assert signal is not None
    assert signal.direction == "long"


@pytest.mark.asyncio
async def test_ema_crossover_generates_bearish_signal() -> None:
    strategy = EMACrossoverStrategy()
    snapshot = build_snapshot(closes=[100.0] * 21 + [70.0], last_high=80.0, last_low=68.0)

    signal = await strategy.generate_signal(snapshot)

    assert signal is not None
    assert signal.direction == "short"


@pytest.mark.asyncio
async def test_ema_crossover_returns_none_without_cross() -> None:
    strategy = EMACrossoverStrategy()
    snapshot = build_snapshot(closes=[100.0] * 22, last_high=101.0, last_low=99.0)

    signal = await strategy.generate_signal(snapshot)

    assert signal is None


@pytest.mark.asyncio
async def test_ema_crossover_raises_for_too_few_candles() -> None:
    strategy = EMACrossoverStrategy()
    snapshot = build_snapshot(closes=[100.0] * 21, last_high=101.0, last_low=99.0)

    with pytest.raises(ValueError, match="candles"):
        await strategy.generate_signal(snapshot)


@pytest.mark.asyncio
async def test_ema_crossover_returns_none_below_min_rrr() -> None:
    strategy = EMACrossoverStrategy(min_rrr=2.5)
    snapshot = build_snapshot(closes=[100.0] * 21 + [130.0], last_high=132.0, last_low=120.0)

    signal = await strategy.generate_signal(snapshot)

    assert signal is None
