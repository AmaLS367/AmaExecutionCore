from __future__ import annotations

from dataclasses import dataclass
from math import log
from statistics import median, stdev

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.contracts import BaseStrategy, StrategySignal


def _calculate_ema(values: list[float], period: int) -> list[float]:
    multiplier = 2 / (period + 1)
    ema_values = [values[0]]
    for price in values[1:]:
        ema_values.append((price - ema_values[-1]) * multiplier + ema_values[-1])
    return ema_values


def _calculate_atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    if len(closes) <= period:
        raise ValueError("Not enough candles to calculate ATR.")

    true_ranges = [
        max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        )
        for index in range(1, len(closes))
    ]

    atr_values = [sum(true_ranges[:period]) / period]
    for true_range in true_ranges[period:]:
        atr_values.append(((atr_values[-1] * (period - 1)) + true_range) / period)
    return atr_values


@dataclass(slots=True)
class BreakoutStrategy(BaseStrategy[MarketSnapshot]):
    lookback_period: int = 20
    volume_multiplier: float = 2.0
    atr_period: int = 14
    atr_stop_multiplier: float = 1.0
    min_rrr: float = 2.0
    trend_ema_period: int = 50
    volatility_skip_threshold: float = 0.04
    strategy_version: str = "breakout-v1"

    @property
    def required_candle_count(self) -> int:
        return max(self.trend_ema_period + 1, self.lookback_period + 1, self.atr_period + 1, 21)

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        self._validate_snapshot(snapshot)

        closes = list(snapshot.closes)
        highs = list(snapshot.highs)
        lows = list(snapshot.lows)
        volumes = list(snapshot.volumes)
        if self._realized_volatility(snapshot) >= self.volatility_skip_threshold:
            return None

        ema_values = _calculate_ema(closes, self.trend_ema_period)
        current_ema = ema_values[-1]
        current_atr = _calculate_atr(highs, lows, closes, self.atr_period)[-1]
        current_close = closes[-1]
        current_volume = volumes[-1]
        range_high = max(highs[-self.lookback_period - 1 : -1])
        range_low = min(lows[-self.lookback_period - 1 : -1])
        baseline_volumes = volumes[-21:-1] if len(volumes) >= 21 else volumes[-20:]
        volume_threshold = self.volume_multiplier * median(baseline_volumes)

        if current_close > range_high and current_close > current_ema and current_volume >= volume_threshold:
            stop = current_close - (self.atr_stop_multiplier * current_atr)
            target = current_close + (self.min_rrr * abs(current_close - stop))
            return self._build_signal(
                snapshot=snapshot,
                direction="long",
                entry=current_close,
                stop=stop,
                target=target,
                range_high=range_high,
                range_low=range_low,
                current_ema=current_ema,
                current_atr=current_atr,
            )

        if current_close < range_low and current_close < current_ema and current_volume >= volume_threshold:
            stop = current_close + (self.atr_stop_multiplier * current_atr)
            target = current_close - (self.min_rrr * abs(current_close - stop))
            return self._build_signal(
                snapshot=snapshot,
                direction="short",
                entry=current_close,
                stop=stop,
                target=target,
                range_high=range_high,
                range_low=range_low,
                current_ema=current_ema,
                current_atr=current_atr,
            )

        return None

    def _build_signal(
        self,
        *,
        snapshot: MarketSnapshot,
        direction: str,
        entry: float,
        stop: float,
        target: float,
        range_high: float,
        range_low: float,
        current_ema: float,
        current_atr: float,
    ) -> StrategySignal | None:
        risk = abs(entry - stop)
        if risk == 0:
            return None
        reward = abs(target - entry)
        rrr = reward / risk
        if rrr < self.min_rrr:
            return None
        return StrategySignal(
            symbol=snapshot.symbol,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
            reason=f"{direction}_breakout",
            strategy_version=self.strategy_version,
            indicators_snapshot={
                "range_high": range_high,
                "range_low": range_low,
                "ema": current_ema,
                "atr": current_atr,
                "rrr": rrr,
            },
        )

    @staticmethod
    def _realized_volatility(snapshot: MarketSnapshot) -> float:
        closes = list(snapshot.closes)[-20:]
        if len(closes) < 2:
            return 0.0
        returns = [
            log(closes[index] / closes[index - 1])
            for index in range(1, len(closes))
            if closes[index - 1] > 0
        ]
        if len(returns) < 2:
            return 0.0
        return stdev(returns)

    def _validate_snapshot(self, snapshot: MarketSnapshot) -> None:
        minimum_candles = self.required_candle_count
        if len(snapshot.closes) < minimum_candles:
            raise ValueError(f"At least {minimum_candles} candles are required.")
        if len(snapshot.highs) != len(snapshot.closes) or len(snapshot.lows) != len(snapshot.closes):
            raise ValueError("Closes, highs, and lows must have equal lengths.")
