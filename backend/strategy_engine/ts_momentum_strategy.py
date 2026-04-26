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


def regime_allows(
    *,
    closes: list[float],
    ema_fast: list[float],
    ema_slow: list[float],
) -> bool:
    return (
        closes[-1] > ema_fast[-1] > ema_slow[-1]
        and ema_fast[-1] > ema_fast[-2]
        and ema_slow[-1] > ema_slow[-2]
    )


@dataclass(slots=True)
class TSMomentumStrategy(BaseStrategy[MarketSnapshot]):
    ema_fast_period: int = 96
    ema_slow_period: int = 288
    momentum_lookback: int = 24
    atr_period: int = 20
    volume_multiplier: float = 1.5
    volatility_skip_threshold: float = 0.03
    entry_breakout_bars: int = 4
    atr_initial_stop_multiplier: float = 1.5
    atr_trail_multiplier: float = 2.0
    min_rrr: float = 1.5
    strategy_version: str = "ts-momentum-v1"

    @property
    def required_candle_count(self) -> int:
        return max(self.ema_slow_period + 5, self.momentum_lookback + self.atr_period + 5, 50)

    def compute_momentum_score(self, snapshot: MarketSnapshot) -> float | None:
        self._validate_snapshot(snapshot)
        closes = list(snapshot.closes)
        ema_fast = _calculate_ema(closes, self.ema_fast_period)
        ema_slow = _calculate_ema(closes, self.ema_slow_period)
        if not regime_allows(closes=closes, ema_fast=ema_fast, ema_slow=ema_slow):
            return None
        if self._realized_volatility(snapshot) >= self.volatility_skip_threshold:
            return None

        atr_values = _calculate_atr(list(snapshot.highs), list(snapshot.lows), closes, self.atr_period)
        current_atr = atr_values[-1]
        if current_atr <= 0:
            return None

        momentum_sum = sum(
            log(closes[index] / closes[index - 1])
            for index in range(len(closes) - self.momentum_lookback, len(closes))
            if closes[index - 1] > 0 and closes[index] > 0
        )
        return momentum_sum / current_atr

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        score = self.compute_momentum_score(snapshot)
        if score is None:
            return None

        closes = list(snapshot.closes)
        volumes = list(snapshot.volumes)
        current_close = closes[-1]
        current_volume = volumes[-1]
        volume_threshold = self.volume_multiplier * median(volumes[-20:])
        if current_volume < volume_threshold:
            return None

        atr_values = _calculate_atr(list(snapshot.highs), list(snapshot.lows), closes, self.atr_period)
        current_atr = atr_values[-1]
        stop = current_close - (self.atr_initial_stop_multiplier * current_atr)
        if stop >= current_close:
            return None
        target = current_close + (self.min_rrr * abs(current_close - stop))
        return StrategySignal(
            symbol=snapshot.symbol,
            direction="long",
            entry=current_close,
            stop=stop,
            target=target,
            reason="ts_momentum_long",
            strategy_version=self.strategy_version,
            indicators_snapshot={
                "score": score,
                "atr": current_atr,
                "rrr": self.min_rrr,
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
            if closes[index - 1] > 0 and closes[index] > 0
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
