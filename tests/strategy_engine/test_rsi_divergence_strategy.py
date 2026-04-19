from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.rsi_divergence_strategy import RSIDivergenceStrategy


def _build_snapshot(closes: list[float]) -> MarketSnapshot:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    candles = tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=5 * i),
            high=closes[i] + 0.5,
            low=closes[i] - 0.5,
            close=closes[i],
            volume=100.0,
        )
        for i in range(len(closes))
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="5", candles=candles)


def _bullish_divergence_closes() -> list[float]:
    """
    53 candles that create bullish RSI divergence:
    - First swing low: sharp decline → very oversold RSI (≈ 0)
    - Second swing low: lower price but less aggressive decline → higher RSI (≈ 26)

    Structure:
      [100.0] * 37  flat warmup
      [95.0, 90.0]  sharp approach to dip 1
      [88.0]        swing low 1 (price=88, RSI≈0)
      [92.0, 96.0]  recovery
      [99.0, 100.0, 100.5, 100.0, 99.5]  peak
      [98.0, 96.0]  gradual approach to dip 2
      [85.0]        swing low 2 (price=85 < 88, RSI≈26 > 0) ← DIVERGENCE
      [88.0, 92.0]  recovery after swing low 2 (wing candles)
      [95.0]        current entry candle
    Total: 37+2+1+2+5+2+1+2+1 = 53
    """
    return (
        [100.0] * 37
        + [95.0, 90.0]
        + [88.0]
        + [92.0, 96.0]
        + [99.0, 100.0, 100.5, 100.0, 99.5]
        + [98.0, 96.0]
        + [85.0]
        + [88.0, 92.0]
        + [95.0]
    )


def _bearish_divergence_closes() -> list[float]:
    """
    53 candles that create bearish RSI divergence (mirror of bullish):
    - First swing high: very sharp rise → RSI ≈ 100 (extremely overbought)
    - Second swing high: gradual rise to higher price → RSI noticeably lower

    Structure:
      [100.0] * 37  flat warmup
      [112.0, 118.0]  very sharp approach (+18 in 2 candles) to peak 1
      [120.0]         swing high 1 (price=120, RSI≈100)
      [114.0, 108.0]  pullback
      [103.0, 101.0, 100.0, 101.0, 102.0]  trough
      [104.0, 106.0]  gentle approach to peak 2
      [122.0]         swing high 2 (price=122 > 120, RSI lower) ← DIVERGENCE
      [118.0, 114.0]  pullback after swing high 2 (wing candles)
      [110.0]         current entry candle
    Total: 37+2+1+2+5+2+1+2+1 = 53
    """
    return (
        [100.0] * 37
        + [112.0, 118.0]
        + [120.0]
        + [114.0, 108.0]
        + [103.0, 101.0, 100.0, 101.0, 102.0]
        + [104.0, 106.0]
        + [122.0]
        + [118.0, 114.0]
        + [110.0]
    )


@pytest.mark.asyncio
async def test_generates_long_signal_on_bullish_divergence() -> None:
    strategy = RSIDivergenceStrategy()
    closes = _bullish_divergence_closes()

    signal = await strategy.generate_signal(_build_snapshot(closes))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.symbol == "BTCUSDT"
    assert signal.entry == closes[-1]
    assert signal.stop < signal.entry
    assert signal.target > signal.entry
    assert signal.reason == "rsi_divergence_bullish"
    assert signal.strategy_version == "rsi-divergence-v1"
    assert signal.indicators_snapshot is not None
    snapshot = signal.indicators_snapshot
    snapshot_keys = set(snapshot)
    assert "rsi_at_swing1" in snapshot_keys
    assert "rsi_at_swing2" in snapshot_keys
    # Key divergence property: RSI at second swing low > RSI at first swing low
    assert cast(float, snapshot["rsi_at_swing2"]) > cast(float, snapshot["rsi_at_swing1"])
    # And price at second swing low < price at first swing low
    assert cast(float, snapshot["price_at_swing2"]) < cast(float, snapshot["price_at_swing1"])


@pytest.mark.asyncio
async def test_generates_short_signal_on_bearish_divergence() -> None:
    strategy = RSIDivergenceStrategy()
    closes = _bearish_divergence_closes()

    signal = await strategy.generate_signal(_build_snapshot(closes))

    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop > signal.entry
    assert signal.target < signal.entry
    assert signal.reason == "rsi_divergence_bearish"
    assert signal.indicators_snapshot is not None
    snapshot = signal.indicators_snapshot
    assert cast(float, snapshot["rsi_at_swing2"]) < cast(float, snapshot["rsi_at_swing1"])
    assert cast(float, snapshot["price_at_swing2"]) > cast(float, snapshot["price_at_swing1"])


@pytest.mark.asyncio
async def test_returns_none_when_no_divergence_both_rsi_and_price_lower() -> None:
    strategy = RSIDivergenceStrategy()
    n = strategy.required_candle_count  # 53

    # Monotonic decline — both price AND RSI make lower lows (no divergence)
    closes = [float(x) for x in range(n + 53, n - 1, -1)]

    signal = await strategy.generate_signal(_build_snapshot(closes[:n]))

    assert signal is None


@pytest.mark.asyncio
async def test_returns_none_when_fewer_than_two_swing_lows() -> None:
    strategy = RSIDivergenceStrategy()
    n = strategy.required_candle_count

    # Smooth monotonic decline — no local minima form
    closes = [float(100 - i * 0.5) for i in range(n)]

    signal = await strategy.generate_signal(_build_snapshot(closes))

    assert signal is None


@pytest.mark.asyncio
async def test_raises_on_insufficient_candles() -> None:
    strategy = RSIDivergenceStrategy()

    with pytest.raises(ValueError, match="candles are required"):
        await strategy.generate_signal(_build_snapshot([100.0] * 10))


def test_required_candle_count_formula() -> None:
    strategy = RSIDivergenceStrategy()
    expected = strategy.rsi_period + strategy.swing_lookback + strategy.swing_wing * 2 + 5
    assert strategy.required_candle_count == expected


def test_required_candle_count_default() -> None:
    strategy = RSIDivergenceStrategy()
    assert strategy.required_candle_count == 53
