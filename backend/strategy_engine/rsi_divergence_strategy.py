from __future__ import annotations

from dataclasses import dataclass

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.contracts import BaseStrategy, StrategySignal


def _calculate_rsi(values: list[float], period: int) -> list[float]:
    if len(values) < period + 1:
        raise ValueError(f"At least {period + 1} closes are required to calculate RSI.")
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values, values[1:]):
        delta = current - previous
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
    return 100.0 - (100.0 / (1.0 + average_gain / average_loss))


def _find_swing_lows(values: list[float], wing: int) -> list[int]:
    result: list[int] = []
    for i in range(wing, len(values) - wing):
        if all(values[i] < values[i - k] for k in range(1, wing + 1)) and all(
            values[i] < values[i + k] for k in range(1, wing + 1)
        ):
            result.append(i)
    return result


def _find_swing_highs(values: list[float], wing: int) -> list[int]:
    result: list[int] = []
    for i in range(wing, len(values) - wing):
        if all(values[i] > values[i - k] for k in range(1, wing + 1)) and all(
            values[i] > values[i + k] for k in range(1, wing + 1)
        ):
            result.append(i)
    return result


@dataclass(slots=True)
class RSIDivergenceStrategy(BaseStrategy[MarketSnapshot]):
    rsi_period: int = 14
    swing_wing: int = 2
    swing_lookback: int = 30
    min_rrr: float = 1.5
    target_rrr: float = 1.5

    @property
    def required_candle_count(self) -> int:
        return self.rsi_period + self.swing_lookback + self.swing_wing * 2 + 5

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        self._validate_snapshot(snapshot)

        closes = list(snapshot.closes)
        lows = list(snapshot.lows)
        highs = list(snapshot.highs)

        rsi = _calculate_rsi(closes, self.rsi_period)

        search_size = self.swing_lookback + self.swing_wing * 2
        search_closes = closes[-search_size:]
        search_rsi = rsi[-search_size:]

        swing_low_idxs = _find_swing_lows(search_closes, self.swing_wing)
        swing_high_idxs = _find_swing_highs(search_closes, self.swing_wing)

        # Proximity guard: second swing must be near the end of the search window
        # so the signal is actionable (not stale).
        near_threshold = search_size - self.swing_wing * 2 - 3

        signal = self._check_bullish_divergence(
            snapshot=snapshot,
            closes=closes,
            lows=lows,
            search_closes=search_closes,
            search_rsi=search_rsi,
            swing_low_idxs=swing_low_idxs,
            search_size=search_size,
            near_threshold=near_threshold,
        )
        if signal is not None:
            return signal

        return self._check_bearish_divergence(
            snapshot=snapshot,
            closes=closes,
            highs=highs,
            search_closes=search_closes,
            search_rsi=search_rsi,
            swing_high_idxs=swing_high_idxs,
            search_size=search_size,
            near_threshold=near_threshold,
        )

    def _check_bullish_divergence(
        self,
        *,
        snapshot: MarketSnapshot,
        closes: list[float],
        lows: list[float],
        search_closes: list[float],
        search_rsi: list[float],
        swing_low_idxs: list[int],
        search_size: int,
        near_threshold: int,
    ) -> StrategySignal | None:
        if len(swing_low_idxs) < 2:
            return None
        i1, i2 = swing_low_idxs[-2], swing_low_idxs[-1]
        if i2 < near_threshold:
            return None
        price_lower_low = search_closes[i2] < search_closes[i1]
        rsi_higher_low = search_rsi[i2] > search_rsi[i1]
        if not (price_lower_low and rsi_higher_low):
            return None

        full_i2 = len(closes) - search_size + i2
        stop = lows[full_i2] * 0.999
        entry = closes[-1]
        if stop >= entry:
            return None
        risk = entry - stop
        target = entry + self.target_rrr * risk
        rrr = (target - entry) / risk
        if rrr < self.min_rrr - 1e-9:
            return None
        return StrategySignal(
            symbol=snapshot.symbol,
            direction="long",
            entry=entry,
            stop=stop,
            target=target,
            reason="rsi_divergence_bullish",
            strategy_version="rsi-divergence-v1",
            indicators_snapshot={
                "rsi_at_swing1": search_rsi[i1],
                "rsi_at_swing2": search_rsi[i2],
                "price_at_swing1": search_closes[i1],
                "price_at_swing2": search_closes[i2],
                "rrr": rrr,
            },
        )

    def _check_bearish_divergence(
        self,
        *,
        snapshot: MarketSnapshot,
        closes: list[float],
        highs: list[float],
        search_closes: list[float],
        search_rsi: list[float],
        swing_high_idxs: list[int],
        search_size: int,
        near_threshold: int,
    ) -> StrategySignal | None:
        if len(swing_high_idxs) < 2:
            return None
        i1, i2 = swing_high_idxs[-2], swing_high_idxs[-1]
        if i2 < near_threshold:
            return None
        price_higher_high = search_closes[i2] > search_closes[i1]
        rsi_lower_high = search_rsi[i2] < search_rsi[i1]
        if not (price_higher_high and rsi_lower_high):
            return None

        full_i2 = len(closes) - search_size + i2
        stop = highs[full_i2] * 1.001
        entry = closes[-1]
        if stop <= entry:
            return None
        risk = stop - entry
        target = entry - self.target_rrr * risk
        rrr = (entry - target) / risk
        if rrr < self.min_rrr - 1e-9:
            return None
        return StrategySignal(
            symbol=snapshot.symbol,
            direction="short",
            entry=entry,
            stop=stop,
            target=target,
            reason="rsi_divergence_bearish",
            strategy_version="rsi-divergence-v1",
            indicators_snapshot={
                "rsi_at_swing1": search_rsi[i1],
                "rsi_at_swing2": search_rsi[i2],
                "price_at_swing1": search_closes[i1],
                "price_at_swing2": search_closes[i2],
                "rrr": rrr,
            },
        )

    def _validate_snapshot(self, snapshot: MarketSnapshot) -> None:
        minimum = self.required_candle_count
        if len(snapshot.closes) < minimum:
            raise ValueError(f"At least {minimum} candles are required.")
        if len(snapshot.highs) != len(snapshot.closes) or len(snapshot.lows) != len(snapshot.closes):
            raise ValueError("Closes, highs, and lows must have equal lengths.")
