from __future__ import annotations

from dataclasses import dataclass

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.contracts import BaseStrategy, StrategySignal
from backend.strategy_engine.htf_trend_filter import (
    aggregate_complete_candles,
    htf_required_source_candles,
    is_bullish_htf_trend,
)
from backend.strategy_engine.rsi_ema_strategy import RSIEMAStrategy


@dataclass(slots=True)
class RSIEMASpotV2Strategy(BaseStrategy[MarketSnapshot]):
    def __init__(
        self,
        *,
        signal_interval: str = "15",
        htf_interval: str = "240",
        htf_ema_period: int = 50,
        htf_require_slope: bool = True,
        ema_period: int = 50,
        rsi_period: int = 14,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 65.0,
        min_rrr: float = 1.5,
        target_rrr: float = 1.5,
    ) -> None:
        self._signal_interval = signal_interval
        self._htf_interval = htf_interval.strip()
        self._htf_ema_period = htf_ema_period
        self._htf_require_slope = htf_require_slope
        self._htf_slope_lookback = 5
        self._inner = RSIEMAStrategy(
            ema_period=ema_period,
            rsi_period=rsi_period,
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            min_rrr=min_rrr,
            target_rrr=target_rrr,
        )

    @property
    def required_candle_count(self) -> int:
        if not self._htf_interval:
            return self._inner.required_candle_count
        return max(
            self._inner.required_candle_count,
            htf_required_source_candles(
                source_interval=self._signal_interval,
                target_interval=self._htf_interval,
                ema_period=self._htf_ema_period,
                slope_lookback=self._htf_slope_lookback,
            ),
        )

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        if self._htf_interval and not self._passes_htf_filter(snapshot):
            return None

        signal = await self._inner.generate_signal(snapshot)
        if signal is None or signal.direction == "short":
            return None
        signal.strategy_version = "rsi-ema-spot-v2"
        return signal

    def _passes_htf_filter(self, snapshot: MarketSnapshot) -> bool:
        htf_candles = aggregate_complete_candles(
            snapshot.candles,
            source_interval=self._signal_interval,
            target_interval=self._htf_interval,
        )
        return is_bullish_htf_trend(
            htf_candles,
            ema_period=self._htf_ema_period,
            slope_lookback=self._htf_slope_lookback,
            require_slope=self._htf_require_slope,
        )
