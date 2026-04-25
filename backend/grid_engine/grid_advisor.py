from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from backend.grid_engine.grid_backtester import RawCandle
from backend.grid_engine.grid_config import GridConfig


@dataclass(frozen=True, slots=True)
class AdvisorCandle:
    high: float
    low: float
    close: float


def suggest_grid(
    candles: Sequence[RawCandle],
    capital_usdt: float,
    min_step_pct: float = 0.005,
    target_n_levels: int = 10,
    atr_period: int = 20,
    atr_multiplier: float = 2.0,
    symbol: str = "XRPUSDT",
) -> GridConfig:
    if len(candles) < atr_period + 1:
        raise ValueError("ATR grid suggestion requires at least atr_period + 1 candles.")

    parsed_candles = [_parse_advisor_candle(candle) for candle in candles]
    atr = _calculate_atr(parsed_candles, atr_period=atr_period)
    current_price = parsed_candles[-1].close
    half_range = atr_multiplier * atr
    p_min = current_price - half_range
    p_max = current_price + half_range
    if p_min <= 0:
        raise ValueError("Suggested grid lower bound must be positive.")

    n_levels = max(4, target_n_levels)
    while n_levels > 4 and ((p_max - p_min) / n_levels) / p_min < min_step_pct:
        n_levels -= 1
    if ((p_max - p_min) / n_levels) / p_min < min_step_pct:
        half_range = _minimum_half_range(
            current_price=current_price,
            n_levels=n_levels,
            min_step_pct=min_step_pct,
        )
        p_min = current_price - half_range
        p_max = current_price + half_range

    return GridConfig(
        symbol=symbol,
        p_min=p_min,
        p_max=p_max,
        n_levels=n_levels,
        capital_usdt=capital_usdt,
    )


def _calculate_atr(candles: Sequence[AdvisorCandle], *, atr_period: int) -> float:
    true_ranges: list[float] = []
    start = len(candles) - atr_period
    for index in range(start, len(candles)):
        current = candles[index]
        previous_close = candles[index - 1].close
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous_close),
                abs(current.low - previous_close),
            ),
        )
    return sum(true_ranges) / atr_period


def _minimum_half_range(*, current_price: float, n_levels: int, min_step_pct: float) -> float:
    target_step_pct = min_step_pct * (1.0 + 1e-12)
    return (target_step_pct * n_levels * current_price) / (2 + target_step_pct * n_levels)


def _parse_advisor_candle(candle: RawCandle) -> AdvisorCandle:
    if isinstance(candle, Mapping):
        return AdvisorCandle(
            high=_float_from_mapping(candle, "high"),
            low=_float_from_mapping(candle, "low"),
            close=_float_from_mapping(candle, "close"),
        )
    if len(candle) < 5:
        raise ValueError(f"Expected candle with at least 5 values, got {len(candle)}.")
    return AdvisorCandle(
        high=_to_float(candle[2]),
        low=_to_float(candle[3]),
        close=_to_float(candle[4]),
    )


def _float_from_mapping(candle: Mapping[str, object], key: str) -> float:
    raw_value = candle.get(key)
    if raw_value is None:
        raise ValueError(f"Candle missing required key: {key}")
    return _to_float(raw_value)


def _to_float(raw_value: object) -> float:
    if isinstance(raw_value, bool):
        raise TypeError(f"Expected numeric candle value, got {raw_value!r}.")
    if isinstance(raw_value, int | float | str):
        return float(raw_value)
    raise ValueError(f"Expected numeric candle value, got {raw_value!r}.")
