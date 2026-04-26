from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from loguru import logger

from backend.backtest import (
    HistoricalReplayRequest,
    HistoricalReplayRunner,
    SimulationExecutionResult,
    SimulationExecutionService,
)
from backend.backtest.datasets import candles_for_lookback, load_dataset
from backend.backtest.replay_runner import SupportsReplayExecutionContext
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.contracts import BaseStrategy
from backend.strategy_engine.factory import build_scalping_strategy

DATASET_PATH = Path("scripts/fixtures/regression/btcusdt_15m_365d.json.gz")
RISK_AMOUNT_USD = 100.0
WIN_RATE_THRESHOLD = Decimal("0.48")
PROFIT_FACTOR_THRESHOLD = Decimal("1.05")


@dataclass(slots=True, frozen=True)
class StrategySummary:
    trades: int
    win_rate: Decimal
    profit_factor: Decimal
    net_pnl: Decimal


def _configure_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "WARNING").upper())


def _format_win_rate(value: Decimal) -> str:
    return f"{value:.2f}"


def _format_profit_factor(value: Decimal) -> str:
    return f"{value:.3f}"


def _format_money(value: Decimal) -> str:
    return format(value, "f")


def _load_last_90_days() -> tuple[MarketCandle, ...]:
    dataset = load_dataset(DATASET_PATH)
    candle_count = candles_for_lookback(interval=dataset.interval, lookback_days=90)
    return dataset.candles[-candle_count:]


def _execution_net_pnl(execution: SimulationExecutionResult) -> Decimal:
    return execution.realized_pnl - execution.fees_paid


async def _run_strategy(
    *,
    strategy: BaseStrategy[MarketSnapshot],
    interval: str,
) -> StrategySummary:
    candles = _load_last_90_days()
    execution_service = cast(
        "SupportsReplayExecutionContext[SimulationExecutionResult]",
        SimulationExecutionService(
            max_hold_candles=20,
            risk_amount_usd=RISK_AMOUNT_USD,
        ),
    )
    runner: HistoricalReplayRunner[SimulationExecutionResult] = HistoricalReplayRunner(
        strategy=strategy,
        execution_service=execution_service,
        max_open_positions=1,
        max_trades_per_day=10,
        cooldown_candles=2,
        hard_pause_consecutive_losses=5,
    )
    result = await runner.replay(
        HistoricalReplayRequest(symbol="BTCUSDT", interval=interval, candles=candles),
    )
    executions = [
        step.execution
        for step in result.steps
        if step.execution is not None
    ]
    net_trade_pnls = [_execution_net_pnl(execution) for execution in executions]
    winning_trades = [net_pnl for net_pnl in net_trade_pnls if net_pnl > 0]
    losing_trades = [net_pnl for net_pnl in net_trade_pnls if net_pnl < 0]
    trade_count = len(net_trade_pnls)
    win_rate = Decimal(0)
    if trade_count:
        win_rate = Decimal(len(winning_trades)) / Decimal(trade_count)

    profit_factor = Decimal(0)
    if winning_trades and not losing_trades:
        profit_factor = Decimal("Infinity")
    elif losing_trades:
        profit_factor = sum(winning_trades, Decimal(0)) / abs(sum(losing_trades, Decimal(0)))

    return StrategySummary(
        trades=trade_count,
        win_rate=win_rate,
        profit_factor=profit_factor,
        net_pnl=sum(net_trade_pnls, Decimal(0)),
    )


def _passes_thresholds(summary: StrategySummary) -> bool:
    return summary.win_rate >= WIN_RATE_THRESHOLD and summary.profit_factor >= PROFIT_FACTOR_THRESHOLD


async def main() -> None:
    _configure_logging()
    dataset = load_dataset(DATASET_PATH)
    vwap_summary = await _run_strategy(
        strategy=build_scalping_strategy(strategy_name="vwap_reversion_v2", min_rrr=1.3),
        interval=dataset.interval,
    )
    ema_summary = await _run_strategy(
        strategy=build_scalping_strategy(strategy_name="ema_pullback", min_rrr=1.5),
        interval=dataset.interval,
    )

    print("[15M VALIDATION]")
    print(
        "vwap_reversion_v2: "
        f"trades={vwap_summary.trades} "
        f"win_rate={_format_win_rate(vwap_summary.win_rate)} "
        f"pf={_format_profit_factor(vwap_summary.profit_factor)} "
        f"net_pnl=${_format_money(vwap_summary.net_pnl)}",
    )
    print(
        "ema_pullback:      "
        f"trades={ema_summary.trades} "
        f"win_rate={_format_win_rate(ema_summary.win_rate)} "
        f"pf={_format_profit_factor(ema_summary.profit_factor)} "
        f"net_pnl=${_format_money(ema_summary.net_pnl)}",
    )

    if not (_passes_thresholds(vwap_summary) or _passes_thresholds(ema_summary)):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
