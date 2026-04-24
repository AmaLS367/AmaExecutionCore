from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.contracts import BaseStrategy, StrategySignal


def _calculate_rsi(closes: list[float], period: int) -> list[float]:
    if len(closes) <= period:
        raise ValueError("Not enough closes to calculate RSI.")

    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(closes)):
        delta = closes[index] - closes[index - 1]
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    values: list[float] = []
    if avg_loss == 0:
        values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        values.append(100.0 - (100.0 / (1.0 + rs)))

    for gain, loss in zip(gains[period:], losses[period:], strict=False):
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        if avg_loss == 0:
            values.append(100.0)
            continue
        rs = avg_gain / avg_loss
        values.append(100.0 - (100.0 / (1.0 + rs)))
    return values


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


def _calculate_intraday_vwap(snapshot: MarketSnapshot) -> float | None:
    if not snapshot.candles:
        raise ValueError("VWAP requires candles.")
    current_day = snapshot.candles[-1].opened_at.date()
    cumulative_price_volume = 0.0
    cumulative_volume = 0.0
    for candle in snapshot.candles:
        if candle.opened_at.date() != current_day or candle.volume == 0:
            continue
        typical_price = (candle.high + candle.low + candle.close) / 3.0
        cumulative_price_volume += typical_price * candle.volume
        cumulative_volume += candle.volume
    if cumulative_volume == 0:
        return None
    return cumulative_price_volume / cumulative_volume


def _count_current_utc_day_candles(snapshot: MarketSnapshot) -> int:
    if not snapshot.candles:
        return 0
    current_day = snapshot.candles[-1].opened_at.date()
    return sum(1 for candle in snapshot.candles if candle.opened_at.date() == current_day)


@dataclass(slots=True)
class VWAPReversionStrategy(BaseStrategy[MarketSnapshot]):
    atr_period: int = 14
    rsi_period: int = 7
    min_deviation: float = 0.005
    min_current_day_candles: int = 12

    @property
    def required_candle_count(self) -> int:
        return 50

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        if len(snapshot.candles) < self.required_candle_count:
            raise ValueError("At least 50 candles are required.")

        current_day_candle_count = _count_current_utc_day_candles(snapshot)
        if current_day_candle_count < self.min_current_day_candles:
            logger.debug(
                "VWAPReversionStrategy skipped. symbol={} close={} current_day_candles={} minimum_required={}",
                snapshot.symbol,
                snapshot.last_price,
                current_day_candle_count,
                self.min_current_day_candles,
            )
            return None

        closes = list(snapshot.closes)
        highs = list(snapshot.highs)
        lows = list(snapshot.lows)
        vwap = _calculate_intraday_vwap(snapshot)
        if vwap is None:
            return None
        rsi_values = _calculate_rsi(closes, self.rsi_period)
        atr_values = _calculate_atr(highs, lows, closes, self.atr_period)

        current_close = closes[-1]
        previous_close = closes[-2]
        current_rsi = rsi_values[-1]
        current_atr = atr_values[-1]
        deviation_pct = (current_close - vwap) / vwap

        logger.debug(
            "VWAPReversionStrategy evaluated. symbol={} close={} vwap={} deviation_pct={} rsi={} current_day_candles={}",
            snapshot.symbol,
            current_close,
            vwap,
            deviation_pct,
            current_rsi,
            current_day_candle_count,
        )

        if (
            current_close < (vwap * (1 - self.min_deviation))
            and previous_close < current_close
            and current_rsi < 35.0
        ):
            stop = current_close - (2 * current_atr)
            target = vwap
            return StrategySignal(
                symbol=snapshot.symbol,
                direction="long",
                entry=current_close,
                stop=stop,
                target=target,
                reason="vwap_reversion_long",
                strategy_version="vwap-reversion-v1",
                indicators_snapshot={
                    "vwap": vwap,
                    "rsi": current_rsi,
                    "atr": current_atr,
                },
            )

        if (
            current_close > (vwap * (1 + self.min_deviation))
            and previous_close > current_close
            and current_rsi > 65.0
        ):
            stop = current_close + (2 * current_atr)
            target = vwap
            return StrategySignal(
                symbol=snapshot.symbol,
                direction="short",
                entry=current_close,
                stop=stop,
                target=target,
                reason="vwap_reversion_short",
                strategy_version="vwap-reversion-v1",
                indicators_snapshot={
                    "vwap": vwap,
                    "rsi": current_rsi,
                    "atr": current_atr,
                },
            )

        return None
