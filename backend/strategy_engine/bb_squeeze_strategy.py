from __future__ import annotations

from dataclasses import dataclass

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.contracts import BaseStrategy, StrategySignal


def _calculate_bollinger_bands(
    closes: list[float],
    period: int,
    num_std: float,
) -> tuple[list[float], list[float], list[float]]:
    upper: list[float] = []
    middle: list[float] = []
    lower: list[float] = []
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance ** 0.5
        middle.append(mean)
        upper.append(mean + num_std * std)
        lower.append(mean - num_std * std)
    return upper, middle, lower


def _calculate_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int,
) -> list[float]:
    if len(closes) <= period:
        raise ValueError("Not enough candles to calculate ATR.")
    true_ranges: list[float] = []
    for index in range(1, len(closes)):
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    atr_values = [sum(true_ranges[:period]) / period]
    for true_range in true_ranges[period:]:
        atr_values.append(((atr_values[-1] * (period - 1)) + true_range) / period)
    return atr_values


@dataclass(slots=True)
class BBSqueezeStrategy(BaseStrategy[MarketSnapshot]):
    bb_period: int = 20
    bb_std: float = 2.0
    squeeze_lookback: int = 50
    atr_period: int = 14
    min_rrr: float = 1.5

    @property
    def required_candle_count(self) -> int:
        return self.bb_period + self.squeeze_lookback + self.atr_period + 5

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        self._validate_snapshot(snapshot)

        closes = list(snapshot.closes)
        highs = list(snapshot.highs)
        lows = list(snapshot.lows)

        bb_upper, bb_middle, bb_lower = _calculate_bollinger_bands(closes, self.bb_period, self.bb_std)
        atr_values = _calculate_atr(highs, lows, closes, self.atr_period)

        band_widths = [
            (u - l) / m if m != 0 else 0.0
            for u, l, m in zip(bb_upper, bb_lower, bb_middle)
        ]

        # Historical widths exclude the current and previous BB periods.
        # Squeeze is confirmed when the previous period sat at the historical minimum.
        historical_widths = band_widths[-(self.squeeze_lookback + 2) : -2]
        if not historical_widths:
            return None

        squeeze_threshold = min(historical_widths)
        prev_width = band_widths[-2]
        was_squeeze = prev_width <= squeeze_threshold

        if not was_squeeze:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        current_atr = atr_values[-1]

        if current_close > bb_upper[-1] and current_close > prev_close:
            stop = current_close - 2.0 * current_atr
            if stop >= current_close:
                raise ValueError("Long setup: ATR stop must be below entry.")
            risk = current_close - stop
            target = current_close + risk * self.min_rrr
            rrr = (target - current_close) / risk
            return StrategySignal(
                symbol=snapshot.symbol,
                direction="long",
                entry=current_close,
                stop=stop,
                target=target,
                reason="bb_squeeze_breakout_long",
                strategy_version="bb-squeeze-v1",
                indicators_snapshot={
                    "bb_upper": bb_upper[-1],
                    "bb_lower": bb_lower[-1],
                    "bb_width": band_widths[-1],
                    "atr": current_atr,
                    "rrr": rrr,
                },
            )

        if current_close < bb_lower[-1] and current_close < prev_close:
            stop = current_close + 2.0 * current_atr
            if stop <= current_close:
                raise ValueError("Short setup: ATR stop must be above entry.")
            risk = stop - current_close
            target = current_close - risk * self.min_rrr
            rrr = (current_close - target) / risk
            return StrategySignal(
                symbol=snapshot.symbol,
                direction="short",
                entry=current_close,
                stop=stop,
                target=target,
                reason="bb_squeeze_breakout_short",
                strategy_version="bb-squeeze-v1",
                indicators_snapshot={
                    "bb_upper": bb_upper[-1],
                    "bb_lower": bb_lower[-1],
                    "bb_width": band_widths[-1],
                    "atr": current_atr,
                    "rrr": rrr,
                },
            )

        return None

    def _validate_snapshot(self, snapshot: MarketSnapshot) -> None:
        minimum = self.required_candle_count
        if len(snapshot.closes) < minimum:
            raise ValueError(f"At least {minimum} candles are required.")
        if len(snapshot.highs) != len(snapshot.closes) or len(snapshot.lows) != len(snapshot.closes):
            raise ValueError("Closes, highs, and lows must have equal lengths.")
