from __future__ import annotations

from collections.abc import Callable

from backend.market_data.contracts import MarketSnapshot
from backend.strategy_engine.bb_squeeze_strategy import BBSqueezeStrategy
from backend.strategy_engine.breakout_strategy import BreakoutStrategy
from backend.strategy_engine.contracts import BaseStrategy
from backend.strategy_engine.ema_crossover import EMACrossoverStrategy
from backend.strategy_engine.ema_pullback_strategy import EMAPullbackStrategy
from backend.strategy_engine.rsi_divergence_strategy import RSIDivergenceStrategy
from backend.strategy_engine.rsi_ema_strategy import RSIEMAStrategy
from backend.strategy_engine.ts_momentum_strategy import TSMomentumStrategy
from backend.strategy_engine.vwap_reversion_strategy import VWAPReversionStrategy
from backend.strategy_engine.vwap_reversion_v2 import VWAPReversionStrategyV2


def build_day_trading_strategy(*, strategy_name: str, min_rrr: float) -> BaseStrategy[MarketSnapshot]:
    normalized_name = strategy_name.strip().lower()
    if normalized_name == "rsi_ema":
        target_rrr = max(1.5, min_rrr)
        return RSIEMAStrategy(min_rrr=min_rrr, target_rrr=target_rrr)
    if normalized_name == "ema_crossover":
        return EMACrossoverStrategy(min_rrr=min_rrr)
    raise ValueError(f"Unknown day-trading strategy: {strategy_name!r}")


def build_scalping_strategy(*, strategy_name: str, min_rrr: float) -> BaseStrategy[MarketSnapshot]:
    normalized_name = strategy_name.strip().lower()
    if normalized_name == "rsi_divergence":
        return RSIDivergenceStrategy(min_rrr=min_rrr, target_rrr=max(1.5, min_rrr))

    strategy_factories: dict[str, Callable[[float], BaseStrategy[MarketSnapshot]]] = {
        "vwap_reversion": lambda threshold: VWAPReversionStrategy(min_rrr=threshold),
        "vwap_reversion_v2": lambda threshold: VWAPReversionStrategyV2(min_rrr=threshold),
        "ema_pullback": lambda threshold: EMAPullbackStrategy(min_rrr=threshold),
        "breakout": lambda threshold: BreakoutStrategy(min_rrr=threshold),
        "ts_momentum": lambda threshold: TSMomentumStrategy(min_rrr=threshold),
        "bb_squeeze": lambda threshold: BBSqueezeStrategy(min_rrr=threshold),
    }
    strategy_factory = strategy_factories.get(normalized_name)
    if strategy_factory is None:
        raise ValueError(f"Unknown scalping strategy: {strategy_name!r}")
    return strategy_factory(min_rrr)
