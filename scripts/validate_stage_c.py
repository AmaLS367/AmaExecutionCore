from __future__ import annotations

import asyncio
import os
import re
import subprocess
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
from backend.market_data.contracts import MarketCandle
from backend.strategy_engine.ema_pullback_strategy import EMAPullbackStrategy
from backend.strategy_engine.factory import build_scalping_strategy

DATASET_PATH = Path("scripts/fixtures/regression/btcusdt_5m_365d.json.gz")
RISK_AMOUNT_USD = 100.0
WIN_RATE_THRESHOLD = Decimal("0.48")
PROFIT_FACTOR_THRESHOLD = Decimal("1.05")


@dataclass(slots=True, frozen=True)
class CommandResult:
    passed: bool
    stdout: str
    stderr: str


@dataclass(slots=True, frozen=True)
class RepoCheckSummary:
    pytest_result: CommandResult
    ruff_result: CommandResult
    mypy_result: CommandResult
    pytest_passed: int
    pytest_failed: int


@dataclass(slots=True, frozen=True)
class StrategyRunSummary:
    trades: int
    win_rate: Decimal
    profit_factor: Decimal
    net_pnl: Decimal
    trades_per_day_average: Decimal


def _configure_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "WARNING").upper())


def _run_command(command: list[str]) -> CommandResult:
    completed = subprocess.run(  # noqa: S603 - fixed local repo command
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(
        passed=completed.returncode == 0,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _parse_pytest_counts(output: str) -> tuple[int, int]:
    passed_match = re.search(r"(?P<passed>\d+)\s+passed", output)
    failed_match = re.search(r"(?P<failed>\d+)\s+failed", output)
    passed = int(passed_match.group("passed")) if passed_match else 0
    failed = int(failed_match.group("failed")) if failed_match else 0
    return passed, failed


def _build_repo_check_summary() -> RepoCheckSummary:
    pytest_result = _run_command(["uv", "run", "pytest", "-q", "--tb=short"])
    ruff_result = _run_command(["uv", "run", "ruff", "check", "."])
    mypy_result = _run_command(["uv", "run", "mypy", "."])
    pytest_passed, pytest_failed = _parse_pytest_counts(pytest_result.stdout + pytest_result.stderr)
    return RepoCheckSummary(
        pytest_result=pytest_result,
        ruff_result=ruff_result,
        mypy_result=mypy_result,
        pytest_passed=pytest_passed,
        pytest_failed=pytest_failed,
    )


def _format_decimal(value: Decimal) -> str:
    if value.is_finite():
        return format(value, "f")
    return str(value)


def _format_money(value: Decimal) -> str:
    return f"${_format_decimal(value)}"


def _passfail(*, passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _load_last_90_days() -> tuple[MarketCandle, ...]:
    dataset = load_dataset(DATASET_PATH)
    candle_count = candles_for_lookback(interval=dataset.interval, lookback_days=90)
    return dataset.candles[-candle_count:]


def _execution_net_pnl(execution: SimulationExecutionResult) -> Decimal:
    return execution.realized_pnl - execution.fees_paid


async def _run_ema_pullback() -> StrategyRunSummary:
    candles = _load_last_90_days()
    strategy = cast(
        "EMAPullbackStrategy",
        build_scalping_strategy(strategy_name="ema_pullback", min_rrr=1.5),
    )
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
        HistoricalReplayRequest(symbol="BTCUSDT", interval="5", candles=candles),
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
    trading_days = {candle.opened_at.date().isoformat() for candle in candles}
    trades_per_day_average = Decimal(0)
    if trading_days:
        trades_per_day_average = Decimal(trade_count) / Decimal(len(trading_days))

    return StrategyRunSummary(
        trades=trade_count,
        win_rate=win_rate,
        profit_factor=profit_factor,
        net_pnl=sum(net_trade_pnls, Decimal(0)),
        trades_per_day_average=trades_per_day_average,
    )


def _print_validation_block(summary: StrategyRunSummary, repo_checks: RepoCheckSummary) -> None:
    win_rate_gate = summary.win_rate >= WIN_RATE_THRESHOLD
    profit_factor_gate = summary.profit_factor >= PROFIT_FACTOR_THRESHOLD
    stage_result = (
        win_rate_gate
        and profit_factor_gate
        and repo_checks.pytest_result.passed
        and repo_checks.ruff_result.passed
        and repo_checks.mypy_result.passed
    )

    print("[STAGE C VALIDATION]")
    print()
    print("C2 - ema_pullback isolated backtest (90d BTC 5m, stateful simulator):")
    print(
        f"  ema_pullback: trades={summary.trades} "
        f"win_rate={_format_decimal(summary.win_rate)} "
        f"pf={_format_decimal(summary.profit_factor)} "
        f"net_pnl={_format_money(summary.net_pnl)} "
        f"trades_per_day_avg={_format_decimal(summary.trades_per_day_average)}",
    )
    print()
    print(
        "Gates for ema_pullback:"
        f"\n  win_rate >= 0.48:      {_passfail(passed=win_rate_gate)}  (actual: {_format_decimal(summary.win_rate)})"
        f"\n  profit_factor >= 1.05: {_passfail(passed=profit_factor_gate)}  (actual: {_format_decimal(summary.profit_factor)})",
    )
    print()
    print(f"Pytest: {repo_checks.pytest_passed} passed, {repo_checks.pytest_failed} failed")
    print(f"Ruff:   {_passfail(passed=repo_checks.ruff_result.passed)}")
    print(f"Mypy:   {_passfail(passed=repo_checks.mypy_result.passed)}")
    print()
    print(f"[STAGE C RESULT]: {_passfail(passed=stage_result)}")


def _print_failures(summary: StrategyRunSummary, repo_checks: RepoCheckSummary) -> None:
    if summary.win_rate < WIN_RATE_THRESHOLD:
        print()
        print("[STAGE C FAILURE] win_rate >= 0.48")
        print(f"Actual: {summary.win_rate}")
        print("Expected: win_rate >= 0.48")
        print("Likely cause: backend/strategy_engine/ema_pullback_strategy.py::generate_signal")
    if summary.profit_factor < PROFIT_FACTOR_THRESHOLD:
        print()
        print("[STAGE C FAILURE] profit_factor >= 1.05")
        print(f"Actual: {summary.profit_factor}")
        print("Expected: profit_factor >= 1.05")
        print("Likely cause: backend/strategy_engine/ema_pullback_strategy.py::_build_signal")
    if not repo_checks.pytest_result.passed:
        print()
        print("[STAGE C FAILURE] Pytest")
        print(f"Actual: {repo_checks.pytest_passed} passed, {repo_checks.pytest_failed} failed")
        print("Expected: 0 failed")
        print("Likely cause: tests/ or the Stage C code paths they exercise")
    if not repo_checks.ruff_result.passed:
        print()
        print("[STAGE C FAILURE] Ruff")
        print(f"Actual: {repo_checks.ruff_result.stdout + repo_checks.ruff_result.stderr}")
        print("Expected: All checks passed")
        print("Likely cause: Recently edited Python files in Stage C")
    if not repo_checks.mypy_result.passed:
        print()
        print("[STAGE C FAILURE] Mypy")
        print(f"Actual: {repo_checks.mypy_result.stdout + repo_checks.mypy_result.stderr}")
        print("Expected: Success: no issues found")
        print("Likely cause: Recently edited Python files in Stage C")


async def main() -> None:
    _configure_logging()
    summary = await _run_ema_pullback()
    repo_checks = _build_repo_check_summary()
    _print_validation_block(summary, repo_checks)
    if (
        summary.win_rate < WIN_RATE_THRESHOLD
        or summary.profit_factor < PROFIT_FACTOR_THRESHOLD
        or not repo_checks.pytest_result.passed
        or not repo_checks.ruff_result.passed
        or not repo_checks.mypy_result.passed
    ):
        _print_failures(summary, repo_checks)
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
