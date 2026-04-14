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


def _calculate_rsi(values: list[float], period: int) -> list[float]:
    if len(values) < period + 1:
        raise ValueError(f"At least {period + 1} closes are required to calculate RSI.")

    gains: list[float] = []
    losses: list[float] = []
    for previous_close, current_close in zip(values, values[1:]):
        delta = current_close - previous_close
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    rsi_values = [_to_rsi(average_gain=average_gain, average_loss=average_loss)]

    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period
        rsi_values.append(_to_rsi(average_gain=average_gain, average_loss=average_loss))

    return rsi_values


def _to_rsi(*, average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


@dataclass(slots=True)
class RSIEMAStrategy(BaseStrategy[MarketSnapshot]):
    def __init__(
        self,
        *,
        ema_period: int = 20,
        rsi_period: int = 14,
        rsi_oversold: float = 40.0,
        rsi_overbought: float = 60.0,
        min_rrr: float = 1.5,
        target_rrr: float = 1.5,
    ) -> None:
        self._ema_period = ema_period
        self._rsi_period = rsi_period
        self._rsi_oversold = rsi_oversold
        self._rsi_overbought = rsi_overbought
        self._min_rrr = min_rrr
        self._target_rrr = target_rrr

    @property
    def required_candle_count(self) -> int:
        return self._ema_period + self._rsi_period + 5

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        self._validate_snapshot(snapshot)

        closes = list(snapshot.closes)
        ema_values = _calculate_ema(closes, self._ema_period)
        rsi_values = _calculate_rsi(closes, self._rsi_period)
        current_close = closes[-1]
        current_ema = ema_values[-1]
        previous_rsi = rsi_values[-2]
        current_rsi = rsi_values[-1]

        if current_close > current_ema and previous_rsi <= self._rsi_oversold < current_rsi:
            stop = min(snapshot.lows[-3:])
            return self._build_signal(
                snapshot=snapshot,
                direction="long",
                entry=current_close,
                stop=stop,
                ema=current_ema,
                previous_rsi=previous_rsi,
                current_rsi=current_rsi,
            )

        if current_close < current_ema and previous_rsi >= self._rsi_overbought > current_rsi:
            stop = max(snapshot.highs[-3:])
            return self._build_signal(
                snapshot=snapshot,
                direction="short",
                entry=current_close,
                stop=stop,
                ema=current_ema,
                previous_rsi=previous_rsi,
                current_rsi=current_rsi,
            )

        return None

    def _build_signal(
        self,
        *,
        snapshot: MarketSnapshot,
        direction: str,
        entry: float,
        stop: float,
        ema: float,
        previous_rsi: float,
        current_rsi: float,
    ) -> StrategySignal | None:
        risk = abs(entry - stop)
        if risk == 0:
            raise ValueError("Entry and stop must not be equal.")
        if direction == "long" and stop >= entry:
            raise ValueError("Long setup requires the swing low stop to stay below entry.")
        if direction == "short" and stop <= entry:
            raise ValueError("Short setup requires the swing high stop to stay above entry.")

        target = entry + (risk * self._target_rrr) if direction == "long" else entry - (risk * self._target_rrr)
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
            reason="rsi_ema_confluence",
            strategy_version=f"rsi-ema-{self._ema_period}-{self._rsi_period}",
            indicators_snapshot={
                "ema": ema,
                "previous_rsi": previous_rsi,
                "current_rsi": current_rsi,
                "rrr": rrr,
                "target_rrr": self._target_rrr,
            },
        )

    def _validate_snapshot(self, snapshot: MarketSnapshot) -> None:
        minimum_candles = self.required_candle_count
        if len(snapshot.closes) < minimum_candles:
            raise ValueError(f"At least {minimum_candles} candles are required.")
        if len(snapshot.highs) != len(snapshot.closes) or len(snapshot.lows) != len(snapshot.closes):
            raise ValueError("Closes, highs, and lows must have equal lengths.")
