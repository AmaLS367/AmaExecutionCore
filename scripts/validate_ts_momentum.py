from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["DEBUG"] = "false"
os.environ.setdefault("LOG_LEVEL", "WARNING")

from loguru import logger

from backend.backtest import SimulationExecutionResult, SimulationExecutionService
from backend.backtest.datasets import candles_for_lookback, load_dataset
from backend.backtest.replay_runner import (
    ReplayPortfolioState,
    ReplayScheduledClosure,
    SupportsReplayExecutionContext,
    _apply_scheduled_closures,
    _can_execute_signal,
    _reset_daily_circuit_breaker,
    _track_execution,
)
from backend.backtest.shadow_runner import _to_execute_signal_request
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.relative_strength_orchestrator import RelativeStrengthOrchestrator
from backend.strategy_engine.ts_momentum_strategy import (
    TSMomentumStrategy,
    _calculate_ema,
    regime_allows,
)

DATASETS = {
    "BTCUSDT": Path("scripts/fixtures/regression/btcusdt_15m_365d.json.gz"),
    "ETHUSDT": Path("scripts/fixtures/regression/ethusdt_15m_365d.json.gz"),
    "SOLUSDT": Path("scripts/fixtures/regression/solusdt_15m_365d.json.gz"),
    "XRPUSDT": Path("scripts/fixtures/regression/xrpusdt_15m_365d.json.gz"),
}
RISK_AMOUNT_USD = 100.0
WIN_RATE_THRESHOLD = Decimal("0.50")
PROFIT_FACTOR_THRESHOLD = Decimal("1.05")


@dataclass(slots=True, frozen=True)
class ValidationSummary:
    trades: int
    win_rate: Decimal
    profit_factor: Decimal
    net_pnl: Decimal
    trades_per_day: Decimal
    btc_regime_skips: int
    score_skips: int


def _configure_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "WARNING").upper())


def _load_last_90_days() -> dict[str, tuple[MarketCandle, ...]]:
    datasets = {symbol: load_dataset(path) for symbol, path in DATASETS.items()}
    candle_count = candles_for_lookback(interval="15", lookback_days=90)
    return {symbol: dataset.candles[-candle_count:] for symbol, dataset in datasets.items()}


def _execution_net_pnl(execution: SimulationExecutionResult) -> Decimal:
    return execution.realized_pnl - execution.fees_paid


def _align_candles(
    candles_by_symbol: dict[str, tuple[MarketCandle, ...]],
) -> tuple[list[datetime], dict[str, tuple[MarketCandle, ...]]]:
    timestamp_sets = [
        {candle.opened_at for candle in candles}
        for candles in candles_by_symbol.values()
    ]
    common_timestamps = sorted(set.intersection(*timestamp_sets))
    aligned: dict[str, tuple[MarketCandle, ...]] = {}
    for symbol, candles in candles_by_symbol.items():
        candle_by_timestamp = {candle.opened_at: candle for candle in candles}
        aligned[symbol] = tuple(candle_by_timestamp[timestamp] for timestamp in common_timestamps)
    return common_timestamps, aligned


def _build_snapshots(
    *,
    aligned_candles: dict[str, tuple[MarketCandle, ...]],
    end_index: int,
    required_candle_count: int,
) -> dict[str, MarketSnapshot]:
    start_index = end_index - required_candle_count + 1
    return {
        symbol: MarketSnapshot(
            symbol=symbol,
            interval="15",
            candles=candles[start_index : end_index + 1],
        )
        for symbol, candles in aligned_candles.items()
    }


def _btc_regime_allows(snapshot: MarketSnapshot, strategy: TSMomentumStrategy) -> bool:
    closes = list(snapshot.closes)
    ema_fast = _calculate_ema(closes, strategy.ema_fast_period)
    ema_slow = _calculate_ema(closes, strategy.ema_slow_period)
    return regime_allows(closes=closes, ema_fast=ema_fast, ema_slow=ema_slow)


def _positive_scores(
    *,
    snapshots: dict[str, MarketSnapshot],
    strategies: dict[str, TSMomentumStrategy],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for symbol, snapshot in snapshots.items():
        strategy = strategies.get(symbol)
        if strategy is None:
            continue
        score = strategy.compute_momentum_score(snapshot)
        if score is not None and score > 0:
            scores[symbol] = score
    return scores


def _build_summary(
    *,
    executions: list[SimulationExecutionResult],
    common_timestamps: list[datetime],
    btc_regime_skips: int,
    score_skips: int,
) -> ValidationSummary:
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

    trading_days = {timestamp.date().isoformat() for timestamp in common_timestamps}
    trades_per_day = Decimal(0)
    if trading_days:
        trades_per_day = Decimal(trade_count) / Decimal(len(trading_days))

    return ValidationSummary(
        trades=trade_count,
        win_rate=win_rate,
        profit_factor=profit_factor,
        net_pnl=sum(net_trade_pnls, Decimal(0)),
        trades_per_day=trades_per_day,
        btc_regime_skips=btc_regime_skips,
        score_skips=score_skips,
    )


async def _run_validation() -> ValidationSummary:
    strategies = {symbol: TSMomentumStrategy() for symbol in DATASETS}
    orchestrator = RelativeStrengthOrchestrator(strategies=strategies)
    required_candle_count = max(strategy.required_candle_count for strategy in strategies.values())
    common_timestamps, aligned_candles = _align_candles(_load_last_90_days())
    execution_service = cast(
        "SupportsReplayExecutionContext[SimulationExecutionResult]",
        SimulationExecutionService(
            max_hold_candles=20,
            risk_amount_usd=RISK_AMOUNT_USD,
        ),
    )

    portfolio_state = ReplayPortfolioState(
        open_positions={},
        cooldown_until={},
        daily_trades={},
        consecutive_losses=0,
        session_halted=False,
        current_date_str=None,
    )
    scheduled_closures: dict[int, list[ReplayScheduledClosure]] = {}
    executions: list[SimulationExecutionResult] = []
    btc_regime_skips = 0
    score_skips = 0

    for step_index in range(required_candle_count - 1, len(common_timestamps)):
        current_timestamp = common_timestamps[step_index]
        current_date_str = current_timestamp.date().isoformat()
        _reset_daily_circuit_breaker(state=portfolio_state, current_date_str=current_date_str)
        _apply_scheduled_closures(
            state=portfolio_state,
            scheduled_closures=scheduled_closures,
            step_index=step_index,
            cooldown_candles=2,
            hard_pause_consecutive_losses=5,
        )

        snapshots = _build_snapshots(
            aligned_candles=aligned_candles,
            end_index=step_index,
            required_candle_count=required_candle_count,
        )
        btc_snapshot = snapshots["BTCUSDT"]
        if not _btc_regime_allows(btc_snapshot, strategies["BTCUSDT"]):
            btc_regime_skips += 1
            continue

        positive_scores = _positive_scores(snapshots=snapshots, strategies=strategies)
        if not positive_scores:
            score_skips += 1
            continue

        signal = await orchestrator.select_signal(snapshots, btc_snapshot=btc_snapshot)
        if signal is None:
            continue
        if not _can_execute_signal(
            state=portfolio_state,
            signal=signal,
            step_index=step_index,
            date_str=current_date_str,
            max_open_positions=1,
            max_trades_per_day=10,
        ):
            continue

        future_candles = aligned_candles[signal.symbol][step_index + 1 :]
        execution: SimulationExecutionResult = await execution_service.execute_replay_signal(
            signal=_to_execute_signal_request(signal),
            future_candles=future_candles,
            step_index=step_index,
        )
        _track_execution(
            state=portfolio_state,
            scheduled_closures=scheduled_closures,
            signal=signal,
            execution=execution,
            step_index=step_index,
            entry_date_str=current_date_str,
            cooldown_candles=2,
            hard_pause_consecutive_losses=5,
        )
        executions.append(execution)

    return _build_summary(
        executions=executions,
        common_timestamps=common_timestamps,
        btc_regime_skips=btc_regime_skips,
        score_skips=score_skips,
    )


async def main() -> None:
    _configure_logging()
    summary = await _run_validation()

    print("[TS-MOMENTUM V2 VALIDATION]")
    print("ts_momentum_v1 (90d, 4-symbol 15m, no breakout entry):")
    print(
        f"  trades={summary.trades}  "
        f"win_rate={summary.win_rate:.2f}  "
        f"pf={summary.profit_factor:.3f}  "
        f"net_pnl=${summary.net_pnl}",
    )
    print(
        f"  trades_per_day={summary.trades_per_day:.1f}  "
        f"btc_regime_skips={summary.btc_regime_skips}",
    )

    if summary.win_rate < WIN_RATE_THRESHOLD or summary.profit_factor < PROFIT_FACTOR_THRESHOLD:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
