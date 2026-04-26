from __future__ import annotations

from dataclasses import dataclass

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.contracts import StrategySignal
from backend.strategy_engine.ts_momentum_strategy import (
    TSMomentumStrategy,
    _calculate_ema,
    regime_allows,
)


@dataclass(slots=True)
class RelativeStrengthOrchestrator:
    strategies: dict[str, TSMomentumStrategy]
    btc_symbol: str = "BTCUSDT"

    async def select_signal(
        self,
        snapshots: dict[str, MarketSnapshot],
        btc_snapshot: MarketSnapshot | None = None,
    ) -> StrategySignal | None:
        if btc_snapshot is not None and not self._btc_regime_allows(btc_snapshot):
            return None

        scores = self._positive_scores(snapshots)
        if not scores:
            return None

        winner = max(scores, key=lambda symbol: scores[symbol])
        return await self.strategies[winner].generate_signal(snapshots[winner])

    def _btc_regime_allows(self, snapshot: MarketSnapshot) -> bool:
        btc_strategy = self.strategies.get(self.btc_symbol)
        if btc_strategy is None:
            btc_strategy = TSMomentumStrategy()
        closes = list(snapshot.closes)
        ema_fast = _calculate_ema(closes, btc_strategy.ema_fast_period)
        ema_slow = _calculate_ema(closes, btc_strategy.ema_slow_period)
        return regime_allows(closes=closes, ema_fast=ema_fast, ema_slow=ema_slow)

    def _positive_scores(self, snapshots: dict[str, MarketSnapshot]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for symbol, snapshot in snapshots.items():
            strategy = self.strategies.get(symbol)
            if strategy is None:
                continue
            score = strategy.compute_momentum_score(snapshot)
            if score is not None and score > 0:
                scores[symbol] = score
        return scores
