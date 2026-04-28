from __future__ import annotations

from decimal import Decimal

from backend.backtest import SimulationExecutionResult
from scripts.run_strategy_backtests import _summarize_executions


def test_summarize_executions_uses_net_pnl_for_win_rate_and_profit_factor() -> None:
    summary = _summarize_executions(
        (
            SimulationExecutionResult(
                realized_pnl=Decimal(200),
                fees_paid=Decimal(10),
                slippage=Decimal(0),
                exit_reason="tp_hit",
                hold_candles=1,
            ),
            SimulationExecutionResult(
                realized_pnl=Decimal(-100),
                fees_paid=Decimal(5),
                slippage=Decimal(0),
                exit_reason="sl_hit",
                hold_candles=1,
            ),
        ),
    )

    assert summary.trades == 2
    assert summary.win_rate == Decimal("0.5")
    assert summary.profit_factor == Decimal(190) / Decimal(105)


def test_summarize_executions_excludes_skipped_results_from_trade_count() -> None:
    summary = _summarize_executions(
        (
            SimulationExecutionResult(
                realized_pnl=Decimal(0),
                fees_paid=Decimal(0),
                slippage=Decimal(0),
                exit_reason="rejected_short_signal",
                hold_candles=0,
                status="skipped",
                rejected_short_signal=True,
            ),
            SimulationExecutionResult(
                realized_pnl=Decimal(200),
                fees_paid=Decimal(10),
                slippage=Decimal(0),
                exit_reason="tp_hit",
                hold_candles=1,
            ),
        ),
    )

    assert summary.trades == 1
    assert summary.win_rate == Decimal(1)
    assert summary.profit_factor == Decimal("Infinity")
