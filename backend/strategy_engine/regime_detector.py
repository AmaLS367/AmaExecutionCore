from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypedDict

from backend.market_data.contracts import MarketCandle

RegimeName = Literal["ranging", "trending"]
StrategyName = Literal["vwap_reversion", "ema_crossover"]


class RegimeDetectionResult(TypedDict):
    adx_value: float
    regime: RegimeName
    recommended_strategy: StrategyName


def detect_regime(
    candles: Sequence[MarketCandle],
    *,
    period: int = 14,
) -> RegimeDetectionResult:
    if period <= 0:
        raise ValueError("ADX period must be greater than zero.")
    if len(candles) < period + 1:
        raise ValueError(f"At least {period + 1} candles are required to detect regime.")

    adx_value = _calculate_adx(candles, period=period)
    if adx_value < 25.0:
        return {
            "adx_value": adx_value,
            "regime": "ranging",
            "recommended_strategy": "vwap_reversion",
        }
    return {
        "adx_value": adx_value,
        "regime": "trending",
        "recommended_strategy": "ema_crossover",
    }


def _calculate_adx(candles: Sequence[MarketCandle], *, period: int) -> float:
    true_ranges: list[float] = []
    positive_directional_moves: list[float] = []
    negative_directional_moves: list[float] = []

    for previous_candle, current_candle in zip(candles, candles[1:], strict=False):
        upward_move = current_candle.high - previous_candle.high
        downward_move = previous_candle.low - current_candle.low
        positive_directional_moves.append(
            upward_move if upward_move > downward_move and upward_move > 0.0 else 0.0,
        )
        negative_directional_moves.append(
            downward_move if downward_move > upward_move and downward_move > 0.0 else 0.0,
        )
        true_ranges.append(
            max(
                current_candle.high - current_candle.low,
                abs(current_candle.high - previous_candle.close),
                abs(current_candle.low - previous_candle.close),
            ),
        )

    smoothed_true_range = sum(true_ranges[:period])
    smoothed_positive_dm = sum(positive_directional_moves[:period])
    smoothed_negative_dm = sum(negative_directional_moves[:period])
    dx_values = [
        _calculate_dx(
            true_range=smoothed_true_range,
            positive_dm=smoothed_positive_dm,
            negative_dm=smoothed_negative_dm,
        ),
    ]

    for index in range(period, len(true_ranges)):
        smoothed_true_range = smoothed_true_range - (smoothed_true_range / period) + true_ranges[index]
        smoothed_positive_dm = (
            smoothed_positive_dm - (smoothed_positive_dm / period) + positive_directional_moves[index]
        )
        smoothed_negative_dm = (
            smoothed_negative_dm - (smoothed_negative_dm / period) + negative_directional_moves[index]
        )
        dx_values.append(
            _calculate_dx(
                true_range=smoothed_true_range,
                positive_dm=smoothed_positive_dm,
                negative_dm=smoothed_negative_dm,
            ),
        )

    seed_size = min(period, len(dx_values))
    adx = sum(dx_values[:seed_size]) / seed_size
    for dx_value in dx_values[seed_size:]:
        adx = ((adx * (period - 1)) + dx_value) / period
    return adx


def _calculate_dx(
    *,
    true_range: float,
    positive_dm: float,
    negative_dm: float,
) -> float:
    if true_range <= 0.0:
        return 0.0
    positive_di = (positive_dm / true_range) * 100.0
    negative_di = (negative_dm / true_range) * 100.0
    denominator = positive_di + negative_di
    if denominator <= 0.0:
        return 0.0
    return abs(positive_di - negative_di) / denominator * 100.0
