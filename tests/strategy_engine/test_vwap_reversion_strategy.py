from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.vwap_reversion_strategy import VWAPReversionStrategy


def _build_snapshot(closes: list[float], *, volumes: list[float] | None = None) -> MarketSnapshot:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    candle_volumes = volumes or [100.0] * len(closes)
    candles = tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=5 * index),
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=candle_volumes[index],
        )
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="5", candles=candles)


def _build_snapshot_with_start(
    closes: list[float],
    *,
    start: datetime,
    volumes: list[float] | None = None,
) -> MarketSnapshot:
    candle_volumes = volumes or [100.0] * len(closes)
    candles = tuple(
        MarketCandle(
            opened_at=start + timedelta(minutes=5 * index),
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=candle_volumes[index],
        )
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="5", candles=candles)


def _build_snapshot_with_current_day_count(
    closes: list[float],
    *,
    current_day_count: int,
    volumes: list[float] | None = None,
) -> MarketSnapshot:
    if current_day_count <= 0 or current_day_count >= len(closes):
        raise ValueError("current_day_count must be between 1 and len(closes) - 1.")

    candle_volumes = volumes or [100.0] * len(closes)
    previous_day_count = len(closes) - current_day_count
    previous_day_start = datetime(2024, 1, 1, 20, 45, tzinfo=UTC)
    current_day_start = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)
    candles: list[MarketCandle] = []

    for index in range(previous_day_count):
        close = closes[index]
        candles.append(
            MarketCandle(
                opened_at=previous_day_start + timedelta(minutes=5 * index),
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=candle_volumes[index],
            ),
        )

    for index in range(current_day_count):
        close_index = previous_day_count + index
        close = closes[close_index]
        candles.append(
            MarketCandle(
                opened_at=current_day_start + timedelta(minutes=5 * index),
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=candle_volumes[close_index],
            ),
        )

    return MarketSnapshot(symbol="BTCUSDT", interval="5", candles=tuple(candles))


@pytest.mark.asyncio
async def test_vwap_reversion_returns_long_signal_on_reclaim_from_below_vwap() -> None:
    strategy = VWAPReversionStrategy()
    closes = [100.0] * 47 + [99.0, 98.4, 99.0]
    volumes = [100.0] * 47 + [400.0, 500.0, 600.0]

    signal = await strategy.generate_signal(_build_snapshot(closes, volumes=volumes))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.symbol == "BTCUSDT"
    assert signal.entry == 99.0


@pytest.mark.asyncio
async def test_vwap_reversion_returns_no_signal_without_deviation_or_bounce() -> None:
    strategy = VWAPReversionStrategy()

    signal = await strategy.generate_signal(_build_snapshot([100.0] * 50))

    assert signal is None


@pytest.mark.asyncio
async def test_vwap_reversion_requires_minimum_candles() -> None:
    strategy = VWAPReversionStrategy()

    with pytest.raises(ValueError, match="At least 50 candles"):
        await strategy.generate_signal(_build_snapshot([100.0] * 10))


@pytest.mark.asyncio
async def test_vwap_reversion_skips_signal_when_current_utc_day_has_under_one_hour_of_data() -> None:
    strategy = VWAPReversionStrategy()
    closes = [100.0] * 47 + [99.0, 98.4, 99.0]
    volumes = [100.0] * 47 + [400.0, 500.0, 600.0]

    signal = await strategy.generate_signal(
        _build_snapshot_with_current_day_count(
            closes,
            current_day_count=11,
            volumes=volumes,
        ),
    )

    assert signal is None
