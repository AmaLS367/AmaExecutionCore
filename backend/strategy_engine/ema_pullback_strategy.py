from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from statistics import median

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.contracts import BaseStrategy, StrategySignal


def _calculate_ema(values: list[float], period: int) -> list[float]:
    multiplier = 2 / (period + 1)
    ema_values = [values[0]]
    for price in values[1:]:
        ema_values.append((price - ema_values[-1]) * multiplier + ema_values[-1])
    return ema_values


def _calculate_rsi(closes: list[float], period: int) -> list[float]:
    if len(closes) <= period:
        raise ValueError("Not enough closes to calculate RSI.")

    gains: list[float] = []
    losses: list[float] = []
    for previous_close, current_close in pairwise(closes):
        delta = current_close - previous_close
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    rsi_values: list[float] = []
    if average_loss == 0:
        rsi_values.append(100.0)
    else:
        relative_strength = average_gain / average_loss
        rsi_values.append(100.0 - (100.0 / (1.0 + relative_strength)))

    for gain, loss in zip(gains[period:], losses[period:], strict=False):
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period
        if average_loss == 0:
            rsi_values.append(100.0)
            continue
        relative_strength = average_gain / average_loss
        rsi_values.append(100.0 - (100.0 / (1.0 + relative_strength)))
    return rsi_values


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
class EMAPullbackStrategy(BaseStrategy[MarketSnapshot]):
    fast_ema_period: int = 20
    slow_ema_period: int = 50
    rsi_period: int = 14
    rsi_long_entry_min: float = 40.0
    rsi_short_entry_max: float = 60.0
    rsi_trend_max_long: float = 70.0
    rsi_trend_min_short: float = 30.0
    atr_period: int = 14
    atr_stop_multiplier: float = 1.2
    target_r_multiple: float = 1.8
    min_rrr: float = 1.5
    volume_confirmation_multiplier: float = 1.1
    pullback_atr_tolerance: float = 0.8
    min_trend_strength_candles: int = 5
    strategy_version: str = "ema-pullback-v1"

    @property
    def required_candle_count(self) -> int:
        return max(60, self.slow_ema_period + self.atr_period + 5)

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        self._validate_snapshot(snapshot)

        closes = list(snapshot.closes)
        highs = list(snapshot.highs)
        lows = list(snapshot.lows)
        volumes = list(snapshot.volumes)
        fast_ema = _calculate_ema(closes, self.fast_ema_period)
        slow_ema = _calculate_ema(closes, self.slow_ema_period)
        current_rsi = _calculate_rsi(closes, self.rsi_period)[-1]
        current_atr = _calculate_atr(highs, lows, closes, self.atr_period)[-1]
        current_fast_ema = fast_ema[-1]
        current_slow_ema = slow_ema[-1]
        entry = closes[-1]
        trend_offset = -self.min_trend_strength_candles
        is_uptrend = current_fast_ema > current_slow_ema and fast_ema[trend_offset] > slow_ema[trend_offset]
        is_downtrend = current_fast_ema < current_slow_ema and fast_ema[trend_offset] < slow_ema[trend_offset]

        if is_uptrend:
            return self._build_long_signal(
                snapshot=snapshot,
                entry=entry,
                lows=lows,
                volumes=volumes,
                current_fast_ema=current_fast_ema,
                current_slow_ema=current_slow_ema,
                current_rsi=current_rsi,
                current_atr=current_atr,
            )
        if is_downtrend:
            return self._build_short_signal(
                snapshot=snapshot,
                entry=entry,
                highs=highs,
                volumes=volumes,
                current_fast_ema=current_fast_ema,
                current_slow_ema=current_slow_ema,
                current_rsi=current_rsi,
                current_atr=current_atr,
            )
        return None

    def _build_long_signal(
        self,
        *,
        snapshot: MarketSnapshot,
        entry: float,
        lows: list[float],
        volumes: list[float],
        current_fast_ema: float,
        current_slow_ema: float,
        current_rsi: float,
        current_atr: float,
    ) -> StrategySignal | None:
        pullback_distance = self.pullback_atr_tolerance * current_atr
        has_pullback = any(abs(low - current_fast_ema) <= pullback_distance for low in lows[-3:])
        if not has_pullback or entry <= current_fast_ema:
            return None
        if not (self.rsi_long_entry_min <= current_rsi <= self.rsi_trend_max_long):
            return None
        if not self._volume_confirms(volumes):
            return None

        swing_low = min(lows[-5:])
        atr_stop = entry - (self.atr_stop_multiplier * current_atr)
        stop = max(swing_low - (0.001 * entry), atr_stop)
        if stop >= entry:
            return None
        target = entry + (self.target_r_multiple * (entry - stop))
        return self._build_signal(
            snapshot=snapshot,
            direction="long",
            entry=entry,
            stop=stop,
            target=target,
            current_fast_ema=current_fast_ema,
            current_slow_ema=current_slow_ema,
            current_rsi=current_rsi,
            current_atr=current_atr,
        )

    def _build_short_signal(
        self,
        *,
        snapshot: MarketSnapshot,
        entry: float,
        highs: list[float],
        volumes: list[float],
        current_fast_ema: float,
        current_slow_ema: float,
        current_rsi: float,
        current_atr: float,
    ) -> StrategySignal | None:
        pullback_distance = self.pullback_atr_tolerance * current_atr
        has_pullback = any(abs(high - current_fast_ema) <= pullback_distance for high in highs[-3:])
        if not has_pullback or entry >= current_fast_ema:
            return None
        if not (self.rsi_trend_min_short <= current_rsi <= self.rsi_short_entry_max):
            return None
        if not self._volume_confirms(volumes):
            return None

        swing_high = max(highs[-5:])
        atr_stop = entry + (self.atr_stop_multiplier * current_atr)
        stop = min(swing_high + (0.001 * entry), atr_stop)
        if stop <= entry:
            return None
        target = entry - (self.target_r_multiple * (stop - entry))
        return self._build_signal(
            snapshot=snapshot,
            direction="short",
            entry=entry,
            stop=stop,
            target=target,
            current_fast_ema=current_fast_ema,
            current_slow_ema=current_slow_ema,
            current_rsi=current_rsi,
            current_atr=current_atr,
        )

    def _build_signal(
        self,
        *,
        snapshot: MarketSnapshot,
        direction: str,
        entry: float,
        stop: float,
        target: float,
        current_fast_ema: float,
        current_slow_ema: float,
        current_rsi: float,
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
            reason=f"{direction}_ema_pullback",
            strategy_version=self.strategy_version,
            indicators_snapshot={
                "ema_fast": current_fast_ema,
                "ema_slow": current_slow_ema,
                "rsi": current_rsi,
                "atr": current_atr,
                "rrr": rrr,
            },
        )

    def _volume_confirms(self, volumes: list[float]) -> bool:
        return volumes[-1] >= self.volume_confirmation_multiplier * median(volumes[-20:])

    def _validate_snapshot(self, snapshot: MarketSnapshot) -> None:
        minimum_candles = self.required_candle_count
        if len(snapshot.closes) < minimum_candles:
            raise ValueError(f"At least {minimum_candles} candles are required.")
        if len(snapshot.highs) != len(snapshot.closes) or len(snapshot.lows) != len(snapshot.closes):
            raise ValueError("Closes, highs, and lows must have equal lengths.")
