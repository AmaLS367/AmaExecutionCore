from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import sin

import pytest

from backend.market_data.contracts import MarketCandle
from backend.strategy_engine.regime_detector import detect_regime


def _build_trending_candles(count: int = 60) -> tuple[MarketCandle, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    candles: list[MarketCandle] = []
    for index in range(count):
        close = 100.0 + index * 1.25 + sin(index / 3) * 0.15
        candles.append(
            MarketCandle(
                opened_at=opened_at + timedelta(minutes=index * 5),
                high=close + 1.0,
                low=close - 0.4,
                close=close,
                volume=100.0,
            ),
        )
    return tuple(candles)


def _build_ranging_candles(count: int = 60) -> tuple[MarketCandle, ...]:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    candles: list[MarketCandle] = []
    for index in range(count):
        close = 100.0 + sin(index / 2) * 1.8
        candles.append(
            MarketCandle(
                opened_at=opened_at + timedelta(minutes=index * 5),
                high=close + 0.9,
                low=close - 0.9,
                close=close,
                volume=100.0,
            ),
        )
    return tuple(candles)


def test_detect_regime_identifies_trending_market() -> None:
    result = detect_regime(_build_trending_candles())

    assert result["regime"] == "trending"
    assert result["recommended_strategy"] == "ema_crossover"
    assert result["adx_value"] >= 25.0


def test_detect_regime_identifies_ranging_market() -> None:
    result = detect_regime(_build_ranging_candles())

    assert result["regime"] == "ranging"
    assert result["recommended_strategy"] == "vwap_reversion"
    assert result["adx_value"] < 25.0


def test_detect_regime_requires_enough_candles() -> None:
    with pytest.raises(ValueError, match="candles are required"):
        detect_regime(_build_ranging_candles(count=10))
