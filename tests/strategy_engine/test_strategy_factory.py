from __future__ import annotations

import pytest

from backend.strategy_engine.bb_squeeze_strategy import BBSqueezeStrategy
from backend.strategy_engine.breakout_strategy import BreakoutStrategy
from backend.strategy_engine.ema_crossover import EMACrossoverStrategy
from backend.strategy_engine.ema_pullback_strategy import EMAPullbackStrategy
from backend.strategy_engine.factory import build_day_trading_strategy, build_scalping_strategy
from backend.strategy_engine.rsi_divergence_strategy import RSIDivergenceStrategy
from backend.strategy_engine.rsi_ema_spot_v2_strategy import RSIEMASpotV2Strategy
from backend.strategy_engine.rsi_ema_strategy import RSIEMAStrategy
from backend.strategy_engine.ts_momentum_strategy import TSMomentumStrategy
from backend.strategy_engine.vwap_reversion_strategy import VWAPReversionStrategy


def test_build_day_trading_strategy_returns_rsi_ema_strategy() -> None:
    strategy = build_day_trading_strategy(strategy_name="rsi_ema", min_rrr=2.0)

    assert isinstance(strategy, RSIEMAStrategy)


def test_build_day_trading_strategy_returns_rsi_ema_spot_v2_strategy() -> None:
    strategy = build_day_trading_strategy(
        strategy_name="rsi_ema_spot_v2",
        min_rrr=2.0,
        signal_interval="15",
        htf_interval="240",
        htf_ema_period=20,
    )

    assert isinstance(strategy, RSIEMASpotV2Strategy)


def test_build_day_trading_strategy_returns_ema_crossover_strategy() -> None:
    strategy = build_day_trading_strategy(strategy_name="ema_crossover", min_rrr=2.0)

    assert isinstance(strategy, EMACrossoverStrategy)


def test_build_day_trading_strategy_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="Unknown day-trading strategy"):
        build_day_trading_strategy(strategy_name="unknown", min_rrr=2.0)


def test_build_scalping_strategy_returns_vwap_reversion() -> None:
    strategy = build_scalping_strategy(strategy_name="vwap_reversion", min_rrr=1.5)

    assert isinstance(strategy, VWAPReversionStrategy)


def test_build_scalping_strategy_returns_bb_squeeze() -> None:
    strategy = build_scalping_strategy(strategy_name="bb_squeeze", min_rrr=1.5)

    assert isinstance(strategy, BBSqueezeStrategy)


def test_build_scalping_strategy_returns_rsi_divergence() -> None:
    strategy = build_scalping_strategy(strategy_name="rsi_divergence", min_rrr=1.5)

    assert isinstance(strategy, RSIDivergenceStrategy)


def test_build_scalping_strategy_returns_ema_pullback() -> None:
    strategy = build_scalping_strategy(strategy_name="ema_pullback", min_rrr=1.5)

    assert isinstance(strategy, EMAPullbackStrategy)


def test_build_scalping_strategy_returns_breakout() -> None:
    strategy = build_scalping_strategy(strategy_name="breakout", min_rrr=2.0)

    assert isinstance(strategy, BreakoutStrategy)


def test_build_scalping_strategy_returns_ts_momentum() -> None:
    strategy = build_scalping_strategy(strategy_name="ts_momentum", min_rrr=2.0)

    assert isinstance(strategy, TSMomentumStrategy)


def test_build_scalping_strategy_is_case_insensitive() -> None:
    strategy = build_scalping_strategy(strategy_name="BB_SQUEEZE", min_rrr=1.5)

    assert isinstance(strategy, BBSqueezeStrategy)


def test_build_scalping_strategy_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="Unknown scalping strategy"):
        build_scalping_strategy(strategy_name="unknown", min_rrr=1.5)
