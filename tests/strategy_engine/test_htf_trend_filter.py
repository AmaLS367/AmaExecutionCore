from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.market_data.contracts import MarketCandle
from backend.strategy_engine.htf_trend_filter import (
    aggregate_complete_candles,
    htf_required_source_candles,
    is_bullish_htf_trend,
)


def _candle(*, index: int, close: float, minutes: int = 15) -> MarketCandle:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=index * minutes)
    return MarketCandle(
        opened_at=opened_at,
        open=close - 0.1,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=100.0,
    )


def test_aggregate_complete_candles_ignores_partial_current_bucket() -> None:
    complete = tuple(_candle(index=index, close=100.0 + index) for index in range(32))
    partial = (_candle(index=32, close=1000.0),)

    aggregated = aggregate_complete_candles(
        complete + partial,
        source_interval="15",
        target_interval="240",
    )

    assert len(aggregated) == 2
    assert aggregated[-1].close == complete[-1].close
    assert aggregated[-1].high < 1000.0


def test_htf_required_source_candles_counts_source_buckets() -> None:
    assert htf_required_source_candles(
        source_interval="15",
        target_interval="240",
        ema_period=50,
        slope_lookback=5,
    ) == 880


def test_is_bullish_htf_trend_requires_price_above_ema_and_positive_slope() -> None:
    bullish = tuple(_candle(index=index, close=100.0 + index, minutes=240) for index in range(8))
    below_ema = tuple(_candle(index=index, close=108.0 - index, minutes=240) for index in range(8))

    assert is_bullish_htf_trend(
        bullish,
        ema_period=3,
        slope_lookback=2,
        require_slope=True,
    )
    assert not is_bullish_htf_trend(
        below_ema,
        ema_period=3,
        slope_lookback=2,
        require_slope=True,
    )
