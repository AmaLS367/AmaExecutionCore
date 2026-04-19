from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.bb_squeeze_strategy import BBSqueezeStrategy


def _build_snapshot(
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> MarketSnapshot:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    candles = tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=5 * i),
            high=highs[i] if highs else closes[i] + 1.0,
            low=lows[i] if lows else closes[i] - 1.0,
            close=closes[i],
            volume=100.0,
        )
        for i in range(len(closes))
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="5", candles=candles)


def _make_squeeze_closes(*, n: int, base: float = 100.0, noise: float = 0.05) -> list[float]:
    """Closes that oscillate tightly — creates narrow Bollinger Bands (squeeze)."""
    import math
    return [base + noise * math.sin(i * 0.3) for i in range(n)]


@pytest.mark.asyncio
async def test_generates_long_signal_on_squeeze_breakout_above_upper_band() -> None:
    strategy = BBSqueezeStrategy()
    n = strategy.required_candle_count  # 89

    # n-1 flat candles → band width = 0 at every window → guaranteed squeeze
    closes = [100.0] * (n - 1)
    # Last candle: sharp breakout above upper band
    closes.append(115.0)

    signal = await strategy.generate_signal(_build_snapshot(closes))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.symbol == "BTCUSDT"
    assert signal.entry == closes[-1]
    assert signal.stop < signal.entry
    assert signal.target > signal.entry
    assert signal.reason == "bb_squeeze_breakout_long"
    assert signal.strategy_version == "bb-squeeze-v1"


@pytest.mark.asyncio
async def test_generates_short_signal_on_squeeze_breakout_below_lower_band() -> None:
    strategy = BBSqueezeStrategy()
    n = strategy.required_candle_count

    closes = [100.0] * (n - 1)
    closes.append(85.0)

    signal = await strategy.generate_signal(_build_snapshot(closes))

    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop > signal.entry
    assert signal.target < signal.entry
    assert signal.reason == "bb_squeeze_breakout_short"


@pytest.mark.asyncio
async def test_returns_none_when_in_squeeze_but_no_breakout() -> None:
    strategy = BBSqueezeStrategy()
    n = strategy.required_candle_count

    # All candles flat — squeeze active but no breakout (price == upper == middle)
    closes = [100.0] * n

    signal = await strategy.generate_signal(_build_snapshot(closes))

    assert signal is None


@pytest.mark.asyncio
async def test_returns_none_when_price_moves_down_after_squeeze_breakout_up_attempt() -> None:
    strategy = BBSqueezeStrategy()
    n = strategy.required_candle_count

    closes = _make_squeeze_closes(n=n - 1)
    # Last candle: current close BELOW previous → LONG momentum condition fails
    closes.append(closes[-1] - 0.1)

    signal = await strategy.generate_signal(_build_snapshot(closes))

    # Could still be short if it breaks below lower — but with tiny noise a small dip
    # won't be below the lower band. Accept either None or short (not long).
    assert signal is None or signal.direction == "short"


@pytest.mark.asyncio
async def test_raises_on_insufficient_candles() -> None:
    strategy = BBSqueezeStrategy()

    with pytest.raises(ValueError, match="candles are required"):
        await strategy.generate_signal(_build_snapshot([100.0] * 10))


def test_required_candle_count_formula() -> None:
    strategy = BBSqueezeStrategy()
    assert strategy.required_candle_count == strategy.bb_period + strategy.squeeze_lookback + strategy.atr_period + 5


def test_required_candle_count_default() -> None:
    strategy = BBSqueezeStrategy()
    assert strategy.required_candle_count == 89
