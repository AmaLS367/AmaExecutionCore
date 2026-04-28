from __future__ import annotations

from collections import OrderedDict
from datetime import datetime

from backend.market_data.contracts import MarketCandle
from backend.market_data.intervals import interval_to_minutes
from backend.strategy_engine.rsi_ema_strategy import _calculate_ema


def aggregate_complete_candles(
    candles: tuple[MarketCandle, ...],
    *,
    source_interval: str,
    target_interval: str,
) -> tuple[MarketCandle, ...]:
    source_minutes = interval_to_minutes(source_interval)
    target_minutes = interval_to_minutes(target_interval)
    if target_minutes <= source_minutes or target_minutes % source_minutes != 0:
        raise ValueError("HTF interval must be a multiple of the source interval.")

    candles_per_bucket = target_minutes // source_minutes
    grouped: OrderedDict[datetime, list[MarketCandle]] = OrderedDict()
    for candle in candles:
        opened_at = candle.opened_at
        total_minutes = opened_at.hour * 60 + opened_at.minute
        bucket_minutes = (total_minutes // target_minutes) * target_minutes
        bucket_opened_at = opened_at.replace(
            hour=bucket_minutes // 60,
            minute=bucket_minutes % 60,
            second=0,
            microsecond=0,
        )
        grouped.setdefault(bucket_opened_at, []).append(candle)

    aggregated: list[MarketCandle] = []
    for bucket_opened_at, bucket_candles in grouped.items():
        if len(bucket_candles) != candles_per_bucket:
            continue
        aggregated.append(
            MarketCandle(
                opened_at=bucket_opened_at,
                open=bucket_candles[0].open,
                high=max(candle.high for candle in bucket_candles),
                low=min(candle.low for candle in bucket_candles),
                close=bucket_candles[-1].close,
                volume=sum(candle.volume for candle in bucket_candles),
            ),
        )
    return tuple(aggregated)


def htf_required_source_candles(
    *,
    source_interval: str,
    target_interval: str,
    ema_period: int,
    slope_lookback: int,
) -> int:
    source_minutes = interval_to_minutes(source_interval)
    target_minutes = interval_to_minutes(target_interval)
    if target_minutes <= source_minutes or target_minutes % source_minutes != 0:
        raise ValueError("HTF interval must be a multiple of the source interval.")
    return (ema_period + slope_lookback) * (target_minutes // source_minutes)


def is_bullish_htf_trend(
    candles: tuple[MarketCandle, ...],
    *,
    ema_period: int,
    slope_lookback: int,
    require_slope: bool,
) -> bool:
    if ema_period <= 0:
        raise ValueError("HTF EMA period must be positive.")
    if slope_lookback <= 0:
        raise ValueError("HTF slope lookback must be positive.")
    required_candles = ema_period + (slope_lookback if require_slope else 0)
    if len(candles) < required_candles:
        return False

    closes = [candle.close for candle in candles]
    ema_values = _calculate_ema(closes, ema_period)
    if closes[-1] <= ema_values[-1]:
        return False
    return not require_slope or ema_values[-1] > ema_values[-slope_lookback]
