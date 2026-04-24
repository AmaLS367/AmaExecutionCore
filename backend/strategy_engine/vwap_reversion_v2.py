from __future__ import annotations

from dataclasses import dataclass
from math import log
from statistics import median, stdev

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.contracts import BaseStrategy, StrategySignal
from backend.strategy_engine.regime_detector import detect_regime
from backend.strategy_engine.vwap_reversion_strategy import (
    _calculate_atr,
    _calculate_intraday_vwap,
    _calculate_rsi,
    _count_current_utc_day_candles,
)


@dataclass(slots=True)
class VWAPReversionStrategyV2(BaseStrategy[MarketSnapshot]):
    min_deviation: float = 0.005
    rsi_period: int = 7
    rsi_long_threshold: float = 30.0
    rsi_short_threshold: float = 70.0
    atr_period: int = 14
    atr_stop_multiplier: float = 1.5
    min_rrr: float = 1.3
    maker_fee_rate: float = 0.001
    taker_fee_rate: float = 0.0025
    spread_bps: float = 2.0
    volume_confirmation_multiplier: float = 1.2
    require_reversal_bar: bool = True
    min_current_day_candles: int = 4
    regime_adx_period: int = 14
    regime_adx_threshold: float = 25.0
    volatility_skip_threshold: float = 0.025
    strategy_version: str = "vwap-reversion-v2"

    @property
    def required_candle_count(self) -> int:
        return max(50, self.regime_adx_period + 15 + 1)

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        context = self._signal_context(snapshot)
        if context is None:
            return None

        direction, entry, stop, target, current_rsi, current_atr, adx_value, realized_volatility, final_rrr, vwap = context
        return StrategySignal(
            symbol=snapshot.symbol,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
            reason=f"{direction}_vwap_reversion_v2",
            strategy_version=self.strategy_version,
            indicators_snapshot={
                "vwap": vwap,
                "rsi": current_rsi,
                "atr": current_atr,
                "adx": adx_value,
                "realized_volatility": realized_volatility,
                "rrr": final_rrr,
            },
        )

    def _signal_context(
        self,
        snapshot: MarketSnapshot,
    ) -> tuple[str, float, float, float, float, float, float, float, float, float] | None:
        self._validate_snapshot(snapshot)
        signal_context: tuple[str, float, float, float, float, float, float, float, float, float] | None = None
        has_required_day_candles = _count_current_utc_day_candles(snapshot) >= self.min_current_day_candles
        if has_required_day_candles:
            realized_volatility = self._realized_volatility(snapshot)
            if realized_volatility < self.volatility_skip_threshold:
                adx_value = self._adx_value(snapshot)
                if adx_value < self.regime_adx_threshold:
                    vwap = _calculate_intraday_vwap(snapshot)
                    if vwap is not None:
                        closes = list(snapshot.closes)
                        volumes = list(snapshot.volumes)
                        current_rsi = _calculate_rsi(closes, self.rsi_period)[-1]
                        current_atr = _calculate_atr(
                            list(snapshot.highs),
                            list(snapshot.lows),
                            closes,
                            self.atr_period,
                        )[-1]
                        entry = closes[-1]
                        direction = self._direction(
                            entry=entry,
                            previous_close=closes[-2],
                            current_rsi=current_rsi,
                            vwap=vwap,
                        )
                        if direction is not None and self._volume_confirms(volumes):
                            levels = self._build_trade_levels(
                                direction=direction,
                                entry=entry,
                                atr=current_atr,
                                vwap=vwap,
                            )
                            if levels is not None:
                                stop, target, final_rrr = levels
                                signal_context = (
                                    direction,
                                    entry,
                                    stop,
                                    target,
                                    current_rsi,
                                    current_atr,
                                    adx_value,
                                    realized_volatility,
                                    final_rrr,
                                    vwap,
                                )
        return signal_context

    def _validate_snapshot(self, snapshot: MarketSnapshot) -> None:
        if len(snapshot.candles) < self.required_candle_count:
            raise ValueError(f"At least {self.required_candle_count} candles are required.")

    def _adx_value(self, snapshot: MarketSnapshot) -> float:
        regime_lookback = max(30, self.regime_adx_period + 1)
        return detect_regime(
            snapshot.candles[-regime_lookback:],
            period=self.regime_adx_period,
        )["adx_value"]

    def _direction(
        self,
        *,
        entry: float,
        previous_close: float,
        current_rsi: float,
        vwap: float,
    ) -> str | None:
        deviation = abs((entry - vwap) / vwap)
        if deviation < self.min_deviation:
            return None
        if entry < vwap and current_rsi < self.rsi_long_threshold:
            if self.require_reversal_bar and entry <= previous_close:
                return None
            return "long"
        if entry > vwap and current_rsi > self.rsi_short_threshold:
            if self.require_reversal_bar and entry >= previous_close:
                return None
            return "short"
        return None

    def _volume_confirms(self, volumes: list[float]) -> bool:
        if len(volumes) < 20:
            return False
        return volumes[-1] >= self.volume_confirmation_multiplier * median(volumes[-20:])

    def _build_trade_levels(
        self,
        *,
        direction: str,
        entry: float,
        atr: float,
        vwap: float,
    ) -> tuple[float, float, float] | None:
        round_trip_cost_fraction = (
            self.maker_fee_rate
            + self.taker_fee_rate
            + (self.spread_bps / 10000)
        )
        stop = entry - (self.atr_stop_multiplier * atr) if direction == "long" else entry + (self.atr_stop_multiplier * atr)
        risk = abs(entry - stop)
        if risk == 0:
            return None

        effective_reward_needed = (risk * self.min_rrr) + (entry * round_trip_cost_fraction)
        target = entry + effective_reward_needed if direction == "long" else entry - effective_reward_needed
        if direction == "long" and target > vwap * 1.005:
            return None
        if direction == "short" and target < vwap * 0.995:
            return None

        reward = abs(target - entry)
        final_rrr = reward / risk
        if final_rrr < self.min_rrr:
            return None
        return stop, target, final_rrr

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
