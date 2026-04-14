from __future__ import annotations

import pytest

from backend.strategy_engine.ema_crossover import EMACrossoverStrategy
from backend.strategy_engine.factory import build_day_trading_strategy
from backend.strategy_engine.rsi_ema_strategy import RSIEMAStrategy


def test_build_day_trading_strategy_returns_rsi_ema_strategy() -> None:
    strategy = build_day_trading_strategy(strategy_name="rsi_ema", min_rrr=2.0)

    assert isinstance(strategy, RSIEMAStrategy)


def test_build_day_trading_strategy_returns_ema_crossover_strategy() -> None:
    strategy = build_day_trading_strategy(strategy_name="ema_crossover", min_rrr=2.0)

    assert isinstance(strategy, EMACrossoverStrategy)


def test_build_day_trading_strategy_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="Unknown day-trading strategy"):
        build_day_trading_strategy(strategy_name="unknown", min_rrr=2.0)
