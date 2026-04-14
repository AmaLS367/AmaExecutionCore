from __future__ import annotations

from dataclasses import dataclass

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.contracts import BaseStrategy, StrategySignal


def _calculate_ema(values: list[float], period: int) -> list[float]:
    multiplier = 2 / (period + 1)
    ema_values: list[float] = [values[0]]
    for price in values[1:]:
        ema_values.append((price - ema_values[-1]) * multiplier + ema_values[-1])
    return ema_values


@dataclass(slots=True)
class EMACrossoverStrategy(BaseStrategy[MarketSnapshot]):
    def __init__(self, fast: int = 9, slow: int = 21, min_rrr: float = 2.0) -> None:
        self._fast = fast
        self._slow = slow
        self._min_rrr = min_rrr

    @property
    def required_candle_count(self) -> int:
        return self._slow + 1

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        self._validate_snapshot(snapshot)

        closes = list(snapshot.closes)
        ema_fast = _calculate_ema(closes, self._fast)
        ema_slow = _calculate_ema(closes, self._slow)
        previous_fast = ema_fast[-2]
        previous_slow = ema_slow[-2]
        current_fast = ema_fast[-1]
        current_slow = ema_slow[-1]
        entry = closes[-1]
        lows = snapshot.lows
        highs = snapshot.highs

        if previous_fast <= previous_slow and current_fast > current_slow and entry > current_fast:
            stop = lows[-1]
            if stop >= entry:
                raise ValueError("Long setup requires the last low to be below the entry price.")
            target = entry + 2 * (entry - stop)
            return self._build_signal(
                snapshot=snapshot,
                direction="long",
                entry=entry,
                stop=stop,
                target=target,
                ema_fast=current_fast,
                ema_slow=current_slow,
            )

        if previous_fast >= previous_slow and current_fast < current_slow and entry < current_fast:
            stop = highs[-1]
            if stop <= entry:
                raise ValueError("Short setup requires the last high to be above the entry price.")
            target = entry - 2 * (stop - entry)
            return self._build_signal(
                snapshot=snapshot,
                direction="short",
                entry=entry,
                stop=stop,
                target=target,
                ema_fast=current_fast,
                ema_slow=current_slow,
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
        ema_fast: float,
        ema_slow: float,
    ) -> StrategySignal | None:
        risk = abs(entry - stop)
        if risk == 0:
            raise ValueError("Entry and stop must not be equal.")
        reward = abs(target - entry)
        rrr = reward / risk
        if rrr < self._min_rrr:
            return None
        return StrategySignal(
            symbol=snapshot.symbol,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
            reason=f"ema_{self._fast}_{self._slow}_crossover",
            strategy_version=f"ema-crossover-{self._fast}-{self._slow}",
            indicators_snapshot={
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "rrr": rrr,
            },
        )

    def _validate_snapshot(self, snapshot: MarketSnapshot) -> None:
        minimum_candles = self.required_candle_count
        if len(snapshot.closes) < minimum_candles:
            raise ValueError(f"At least {minimum_candles} candles are required.")
        if len(snapshot.highs) != len(snapshot.closes) or len(snapshot.lows) != len(snapshot.closes):
            raise ValueError("Closes, highs, and lows must have equal lengths.")
