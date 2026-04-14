from __future__ import annotations

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.contracts import BaseStrategy
from backend.strategy_engine.ema_crossover import EMACrossoverStrategy
from backend.strategy_engine.rsi_ema_strategy import RSIEMAStrategy


def build_day_trading_strategy(*, strategy_name: str, min_rrr: float) -> BaseStrategy[MarketSnapshot]:
    normalized_name = strategy_name.strip().lower()
    if normalized_name == "rsi_ema":
        target_rrr = max(1.5, min_rrr)
        return RSIEMAStrategy(min_rrr=min_rrr, target_rrr=target_rrr)
    if normalized_name == "ema_crossover":
        return EMACrossoverStrategy(min_rrr=min_rrr)
    raise ValueError(f"Unknown day-trading strategy: {strategy_name!r}")
