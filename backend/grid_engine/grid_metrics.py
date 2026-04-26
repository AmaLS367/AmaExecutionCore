from __future__ import annotations

import math
import sys
from dataclasses import dataclass

from backend.grid_engine.grid_config import GridConfig
from backend.grid_engine.grid_state import GridState


@dataclass
class GridBacktestResult:
    symbol: str
    backtest_days: int
    config_step_pct: float
    completed_cycles: int
    gross_profit_usdt: float
    total_fees_usdt: float
    net_pnl_usdt: float
    fee_coverage_ratio: float
    annualized_yield_pct: float
    max_unrealized_drawdown_pct: float
    capital_utilization_pct: float
    avg_cycle_profit_pct: float
    days_to_breakeven: float
    pass_regression: bool
    fail_reasons: list[str]

    def print_report(self) -> None:
        status = "PASS" if self.pass_regression else "FAIL"
        _emit(f"[GRID BACKTEST {status}] {self.symbol}")
        _emit(f"  Completed cycles:     {self.completed_cycles}")
        _emit(f"  Gross profit:         ${self.gross_profit_usdt:.4f}")
        _emit(f"  Total fees:           ${self.total_fees_usdt:.4f}")
        _emit(f"  Net PnL:              ${self.net_pnl_usdt:.4f}")
        _emit(f"  Fee coverage ratio:   {self.fee_coverage_ratio:.2f}x  (gate: >=2.0)")
        _emit(f"  Annualized yield:     {self.annualized_yield_pct:.1f}%  (gate: >=10%)")
        _emit(f"  Max unrealized DD:    {self.max_unrealized_drawdown_pct:.1f}%  (gate: <=55%)")
        _emit(f"  Capital utilization:  {self.capital_utilization_pct:.1f}%")
        _emit(f"  Avg cycle profit:     {self.avg_cycle_profit_pct:.3f}%")
        _emit(f"  Days to breakeven:    {self.days_to_breakeven:.1f}d")
        for reason in self.fail_reasons:
            _emit(f"  [FAIL] {reason}")


def evaluate_grid_backtest(
    state: GridState,
    config: GridConfig,
    backtest_days: int,
) -> GridBacktestResult:
    max_unrealized_loss = _max_snapshot_value(state, "unrealized_loss_usdt")
    active_buy_candles = sum(
        1 for snapshot in state.candle_snapshots if _snapshot_float(snapshot, "active_buy_orders") >= 1
    )
    total_candles = len(state.candle_snapshots)

    gross_profit = state.total_gross_profit
    total_fees = state.total_fees_paid
    net_pnl = state.net_pnl
    fee_coverage_ratio = _safe_ratio(gross_profit, total_fees)
    annualized_yield_pct = _annualized_yield_pct(net_pnl, config.capital_usdt, backtest_days)
    max_unrealized_drawdown_pct = _percentage(max_unrealized_loss, config.capital_usdt)
    capital_utilization_pct = _percentage(active_buy_candles, total_candles)
    avg_cycle_profit_pct = _average_cycle_profit_pct(state, config)
    days_to_breakeven = _days_to_breakeven(
        max_unrealized_loss=max_unrealized_loss,
        net_pnl=net_pnl,
        backtest_days=backtest_days,
    )
    fail_reasons = _regression_fail_reasons(
        completed_cycles=state.completed_cycles,
        net_pnl=net_pnl,
        fee_coverage_ratio=fee_coverage_ratio,
        annualized_yield_pct=annualized_yield_pct,
        max_unrealized_drawdown_pct=max_unrealized_drawdown_pct,
    )

    return GridBacktestResult(
        symbol=config.symbol,
        backtest_days=backtest_days,
        config_step_pct=config.step_pct,
        completed_cycles=state.completed_cycles,
        gross_profit_usdt=gross_profit,
        total_fees_usdt=total_fees,
        net_pnl_usdt=net_pnl,
        fee_coverage_ratio=fee_coverage_ratio,
        annualized_yield_pct=annualized_yield_pct,
        max_unrealized_drawdown_pct=max_unrealized_drawdown_pct,
        capital_utilization_pct=capital_utilization_pct,
        avg_cycle_profit_pct=avg_cycle_profit_pct,
        days_to_breakeven=days_to_breakeven,
        pass_regression=not fail_reasons,
        fail_reasons=fail_reasons,
    )


def _regression_fail_reasons(
    *,
    completed_cycles: int,
    net_pnl: float,
    fee_coverage_ratio: float,
    annualized_yield_pct: float,
    max_unrealized_drawdown_pct: float,
) -> list[str]:
    fail_reasons: list[str] = []
    if net_pnl <= 0:
        fail_reasons.append(f"Net PnL {net_pnl:.4f} <= 0")
    if fee_coverage_ratio < 2.0:
        fail_reasons.append(f"Fee coverage ratio {fee_coverage_ratio:.2f} < 2.0")
    if annualized_yield_pct < 10.0:
        fail_reasons.append(f"Annualized yield {annualized_yield_pct:.1f}% < 10%")
    if max_unrealized_drawdown_pct > 55.0:
        fail_reasons.append(f"Max unrealized drawdown {max_unrealized_drawdown_pct:.1f}% > 55%")
    if completed_cycles < 30:
        fail_reasons.append(f"Completed cycles {completed_cycles} < 30")
    return fail_reasons


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return math.inf if numerator > 0 else 0.0
    return numerator / denominator


def _annualized_yield_pct(net_pnl: float, capital_usdt: float, backtest_days: int) -> float:
    if capital_usdt == 0 or backtest_days <= 0:
        return 0.0
    return (net_pnl / capital_usdt) * (365 / backtest_days) * 100


def _percentage(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return (float(numerator) / float(denominator)) * 100


def _average_cycle_profit_pct(state: GridState, config: GridConfig) -> float:
    if state.completed_cycles == 0 or config.capital_per_level == 0:
        return 0.0
    net_cycle_profit = state.net_pnl / state.completed_cycles
    return (net_cycle_profit / config.capital_per_level) * 100


def _days_to_breakeven(*, max_unrealized_loss: float, net_pnl: float, backtest_days: int) -> float:
    if max_unrealized_loss == 0:
        return 0.0
    if net_pnl <= 0 or backtest_days <= 0:
        return math.inf
    return max_unrealized_loss / (net_pnl / backtest_days)


def _max_snapshot_value(state: GridState, key: str) -> float:
    if not state.candle_snapshots:
        return 0.0
    return max(_snapshot_float(snapshot, key) for snapshot in state.candle_snapshots)


def _snapshot_float(snapshot: dict[str, object], key: str) -> float:
    raw_value = snapshot.get(key, 0.0)
    if isinstance(raw_value, bool):
        raise TypeError(f"Expected numeric snapshot value for {key}, got {raw_value!r}.")
    if isinstance(raw_value, int | float | str):
        return float(raw_value)
    raise ValueError(f"Expected numeric snapshot value for {key}, got {raw_value!r}.")


def _emit(message: str) -> None:
    sys.stdout.write(f"{message}\n")
