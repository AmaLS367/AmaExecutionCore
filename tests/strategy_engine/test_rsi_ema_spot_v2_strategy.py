from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.rsi_ema_spot_v2_strategy import RSIEMASpotV2Strategy


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
            opened_at=opened_at + timedelta(minutes=15 * index),
            high=candle_highs[index],
            low=candle_lows[index],
            close=close,
            volume=100.0,
        )
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="15", candles=candles)


@pytest.mark.asyncio
async def test_rsi_ema_spot_v2_emits_long_with_spot_version_when_htf_disabled() -> None:
    strategy = RSIEMASpotV2Strategy(
        htf_interval="",
        ema_period=20,
        rsi_oversold=40.0,
        rsi_overbought=60.0,
    )
    snapshot = _build_snapshot(
        closes=[110.0] * 35 + [105.0, 103.0, 104.0, 110.0],
        lows=[109.0] * 35 + [104.0, 102.0, 103.0, 106.0],
    )

    signal = await strategy.generate_signal(snapshot)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.strategy_version == "rsi-ema-spot-v2"


@pytest.mark.asyncio
async def test_rsi_ema_spot_v2_never_emits_short() -> None:
    strategy = RSIEMASpotV2Strategy(
        htf_interval="",
        ema_period=20,
        rsi_oversold=40.0,
        rsi_overbought=60.0,
    )
    snapshot = _build_snapshot(
        closes=[100.0] * 35 + [105.0, 107.0, 106.0, 100.0],
        highs=[101.0] * 35 + [106.0, 108.0, 107.0, 104.0],
    )

    assert await strategy.generate_signal(snapshot) is None


def test_rsi_ema_spot_v2_required_candles_include_htf_warmup() -> None:
    strategy = RSIEMASpotV2Strategy(
        signal_interval="15",
        htf_interval="240",
        htf_ema_period=50,
    )

    assert strategy.required_candle_count == 880
