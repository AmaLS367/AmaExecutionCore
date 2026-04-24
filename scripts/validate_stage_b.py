from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from backend.strategy_engine.factory import build_scalping_strategy
from backend.strategy_engine.vwap_reversion_strategy import VWAPReversionStrategy
from backend.strategy_engine.vwap_reversion_v2 import VWAPReversionStrategyV2

DATASET_PATH = Path("scripts/fixtures/regression/btcusdt_5m_365d.json.gz")
RISK_AMOUNT_USD = 100.0
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
    rrr_below_minimum: int


@dataclass(slots=True, frozen=True)
class ValidationFailure:
    gate: str
    actual: str
    expected: str
    likely_cause: str


@dataclass(slots=True, frozen=True)
class V2ParameterSet:
    min_deviation: float = 0.005
    rsi_long_threshold: float = 30.0
    rsi_short_threshold: float = 70.0
    volume_confirmation_multiplier: float = 1.2


@dataclass(slots=True, frozen=True)
class V2AttemptRecord:
    label: str
    parameters: V2ParameterSet
    summary: StrategyRunSummary


@dataclass(slots=True, frozen=True)
class StageBValidationData:
    factory_passes_min_rrr: bool
    strategy_validates_rrr: bool
    pytest_bad_rrr_test_passed: bool
    v1_summary: StrategyRunSummary
    v2_summary: StrategyRunSummary
    v2_parameters: V2ParameterSet
    repo_checks: RepoCheckSummary
    attempt_history: tuple[V2AttemptRecord, ...]


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


def _format_decimal(value: Decimal) -> str:
    if value.is_finite():
        return format(value, "f")
    return str(value)


def _passfail(*, passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _load_last_90_days() -> tuple[MarketCandle, ...]:
    dataset = load_dataset(DATASET_PATH)
    candle_count = candles_for_lookback(interval=dataset.interval, lookback_days=90)
    return dataset.candles[-candle_count:]


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


def _configure_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "WARNING").upper())


def _build_bad_rrr_snapshot() -> MarketSnapshot:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC)
    closes = [100.0] * 47 + [99.0, 98.4, 99.0]
    volumes = [100.0] * 47 + [400.0, 500.0, 600.0]
    candles = tuple(
        MarketCandle(
            opened_at=opened_at + timedelta(minutes=5 * index),
            open=close,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=volumes[index],
        )
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(symbol="BTCUSDT", interval="5", candles=candles)


async def _b1_checks() -> tuple[bool, bool, bool]:
    factory_strategy = build_scalping_strategy(strategy_name="vwap_reversion", min_rrr=2.0)
    factory_passes = isinstance(factory_strategy, VWAPReversionStrategy) and factory_strategy.min_rrr == 2.0

    runtime_strategy = VWAPReversionStrategy(min_rrr=2.0)
    runtime_signal = await runtime_strategy.generate_signal(_build_bad_rrr_snapshot())
    strategy_validates_rrr = runtime_signal is None

    pytest_bad_rrr_test = _run_command(
        [
            "uv",
            "run",
            "pytest",
            "tests/strategy_engine/test_vwap_reversion_strategy.py::test_vwap_strategy_rejects_bad_rrr",
            "-q",
            "--tb=short",
        ],
    )
    return factory_passes, strategy_validates_rrr, pytest_bad_rrr_test.passed


def _build_v2_strategy(parameters: V2ParameterSet) -> VWAPReversionStrategyV2:
    return VWAPReversionStrategyV2(
        min_rrr=1.3,
        min_deviation=parameters.min_deviation,
        rsi_long_threshold=parameters.rsi_long_threshold,
        rsi_short_threshold=parameters.rsi_short_threshold,
        volume_confirmation_multiplier=parameters.volume_confirmation_multiplier,
    )


def _execution_net_pnl(execution: SimulationExecutionResult) -> Decimal:
    return execution.realized_pnl - execution.fees_paid


def _signal_rrr(step_signal: object) -> Decimal | None:
    indicators_snapshot = getattr(step_signal, "indicators_snapshot", None)
    if isinstance(indicators_snapshot, dict) and "rrr" in indicators_snapshot:
        return Decimal(str(indicators_snapshot["rrr"]))

    entry = getattr(step_signal, "entry", None)
    stop = getattr(step_signal, "stop", None)
    target = getattr(step_signal, "target", None)
    if entry is None or stop is None or target is None:
        return None

    risk = abs(Decimal(str(entry)) - Decimal(str(stop)))
    reward = abs(Decimal(str(target)) - Decimal(str(entry)))
    if risk == 0:
        return None
    return reward / risk


async def _run_stateful_backtest(
    strategy: VWAPReversionStrategy | VWAPReversionStrategyV2,
) -> StrategyRunSummary:
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

    rrr_below_minimum = 0
    minimum_rrr = Decimal(str(strategy.min_rrr))
    for step in result.steps:
        if step.execution is None or step.signal is None:
            continue
        signal_rrr = _signal_rrr(step.signal)
        if signal_rrr is not None and signal_rrr < minimum_rrr:
            rrr_below_minimum += 1

    return StrategyRunSummary(
        trades=trade_count,
        win_rate=win_rate,
        profit_factor=profit_factor,
        rrr_below_minimum=rrr_below_minimum,
    )


async def _run_v2_attempts() -> tuple[StrategyRunSummary, V2ParameterSet, tuple[V2AttemptRecord, ...]]:
    attempts = [
        ("default", V2ParameterSet()),
        ("attempt_1", V2ParameterSet(min_deviation=0.007)),
        ("attempt_2", V2ParameterSet(min_deviation=0.007, rsi_long_threshold=25.0, rsi_short_threshold=75.0)),
        (
            "attempt_3",
            V2ParameterSet(
                min_deviation=0.007,
                rsi_long_threshold=25.0,
                rsi_short_threshold=75.0,
                volume_confirmation_multiplier=1.5,
            ),
        ),
    ]

    history: list[V2AttemptRecord] = []
    for label, parameters in attempts:
        summary = await _run_stateful_backtest(_build_v2_strategy(parameters))
        history.append(V2AttemptRecord(label=label, parameters=parameters, summary=summary))
        if summary.profit_factor >= PROFIT_FACTOR_THRESHOLD:
            return summary, parameters, tuple(history)
    last_attempt = history[-1]
    return last_attempt.summary, last_attempt.parameters, tuple(history)


def _print_validation_block(data: StageBValidationData) -> None:
    selective_gate = data.v2_summary.trades < data.v1_summary.trades
    win_rate_gate = data.v2_summary.win_rate >= Decimal("0.50")
    profit_factor_gate = data.v2_summary.profit_factor >= PROFIT_FACTOR_THRESHOLD
    rrr_gate = data.v2_summary.rrr_below_minimum == 0
    stage_result = (
        data.factory_passes_min_rrr
        and data.strategy_validates_rrr
        and data.pytest_bad_rrr_test_passed
        and selective_gate
        and win_rate_gate
        and profit_factor_gate
        and rrr_gate
        and data.repo_checks.pytest_result.passed
        and data.repo_checks.ruff_result.passed
        and data.repo_checks.mypy_result.passed
    )

    print("[STAGE B VALIDATION]")
    print()
    print("B1 - RRR Mismatch Fix:")
    print(
        "  factory passes min_rrr to VWAPReversionStrategy:        "
        f"{_passfail(passed=data.factory_passes_min_rrr)}",
    )
    print(
        "  strategy internally validates RRR before returning:     "
        f"{_passfail(passed=data.strategy_validates_rrr)}",
    )
    print(
        "  test: signal with bad RRR returns None:                 "
        f"{_passfail(passed=data.pytest_bad_rrr_test_passed)}",
    )
    print()
    print("B2 - VWAP v2 Backtest (90d BTC 5m, stateful simulator):")
    print(f"  v1 trades:  {data.v1_summary.trades}")
    print(f"  v2 trades:  {data.v2_summary.trades}")
    print(
        "  Constraint: M < N (v2 more selective):                  "
        f"{_passfail(passed=selective_gate)}",
    )
    print()
    print(f"  v1 win_rate: {_format_decimal(data.v1_summary.win_rate)}")
    print(f"  v2 win_rate: {_format_decimal(data.v2_summary.win_rate)}")
    print(
        "  Constraint: v2 win_rate >= 0.50:                        "
        f"{_passfail(passed=win_rate_gate)}",
    )
    print()
    print(f"  v1 profit_factor: {_format_decimal(data.v1_summary.profit_factor)}")
    print(f"  v2 profit_factor: {_format_decimal(data.v2_summary.profit_factor)}")
    print(
        "  Constraint: v2 profit_factor >= 1.05:                   "
        f"{_passfail(passed=profit_factor_gate)}",
    )
    print()
    print(
        '  v2 trades with "RRR below minimum" in replay: '
        f"{data.v2_summary.rrr_below_minimum}         {_passfail(passed=rrr_gate)}",
    )
    print()
    print(f"Pytest: {data.repo_checks.pytest_passed} passed, {data.repo_checks.pytest_failed} failed")
    print(f"Ruff:   {_passfail(passed=data.repo_checks.ruff_result.passed)}")
    print(f"Mypy:   {_passfail(passed=data.repo_checks.mypy_result.passed)}")
    print()
    print(f"[STAGE B RESULT]: {_passfail(passed=stage_result)}")


def _collect_repo_failures(data: StageBValidationData) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    if not data.repo_checks.pytest_result.passed:
        failures.append(
            ValidationFailure(
                gate="Pytest",
                actual=f"{data.repo_checks.pytest_passed} passed, {data.repo_checks.pytest_failed} failed",
                expected="0 failed",
                likely_cause="tests/ or the Stage B code paths they exercise",
            ),
        )
    if not data.repo_checks.ruff_result.passed:
        failures.append(
            ValidationFailure(
                gate="Ruff",
                actual=data.repo_checks.ruff_result.stdout + data.repo_checks.ruff_result.stderr,
                expected="All checks passed",
                likely_cause="Recently edited Python files in Stage B",
            ),
        )
    if not data.repo_checks.mypy_result.passed:
        failures.append(
            ValidationFailure(
                gate="Mypy",
                actual=data.repo_checks.mypy_result.stdout + data.repo_checks.mypy_result.stderr,
                expected="Success: no issues found",
                likely_cause="Recently edited Python files in Stage B",
            ),
        )
    return failures


def _collect_failures(data: StageBValidationData) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    if not data.factory_passes_min_rrr:
        failures.append(
            ValidationFailure(
                gate="factory passes min_rrr to VWAPReversionStrategy",
                actual="Factory returned a strategy without min_rrr == 2.0",
                expected="Factory should pass min_rrr through unchanged",
                likely_cause="backend/strategy_engine/factory.py::build_scalping_strategy",
            ),
        )
    if not data.strategy_validates_rrr:
        failures.append(
            ValidationFailure(
                gate="strategy internally validates RRR before returning",
                actual="Bad-RRR snapshot still produced a signal",
                expected="Bad-RRR snapshot should return None",
                likely_cause="backend/strategy_engine/vwap_reversion_strategy.py::generate_signal",
            ),
        )
    if not data.pytest_bad_rrr_test_passed:
        failures.append(
            ValidationFailure(
                gate="test: signal with bad RRR returns None",
                actual="Focused pytest for bad-RRR signal rejection failed",
                expected="Focused pytest should pass",
                likely_cause="tests/strategy_engine/test_vwap_reversion_strategy.py::test_vwap_strategy_rejects_bad_rrr",
            ),
        )
    if data.v2_summary.trades >= data.v1_summary.trades:
        failures.append(
            ValidationFailure(
                gate="Constraint: M < N (v2 more selective)",
                actual=f"M={data.v2_summary.trades}, N={data.v1_summary.trades}",
                expected="M < N",
                likely_cause="backend/strategy_engine/vwap_reversion_v2.py::generate_signal",
            ),
        )
    if data.v2_summary.win_rate < Decimal("0.50"):
        failures.append(
            ValidationFailure(
                gate="Constraint: v2 win_rate >= 0.50",
                actual=f"v2 win_rate={_format_decimal(data.v2_summary.win_rate)}",
                expected="v2 win_rate >= 0.50",
                likely_cause="backend/strategy_engine/vwap_reversion_v2.py::generate_signal",
            ),
        )
    if data.v2_summary.profit_factor < PROFIT_FACTOR_THRESHOLD:
        failures.append(
            ValidationFailure(
                gate="Constraint: v2 profit_factor >= 1.05",
                actual=f"v2 profit_factor={_format_decimal(data.v2_summary.profit_factor)}",
                expected="v2 profit_factor >= 1.05",
                likely_cause="backend/strategy_engine/vwap_reversion_v2.py::_build_trade_levels",
            ),
        )
    if data.v2_summary.rrr_below_minimum != 0:
        failures.append(
            ValidationFailure(
                gate='v2 trades with "RRR below minimum" in replay: 0',
                actual=str(data.v2_summary.rrr_below_minimum),
                expected="0",
                likely_cause="backend/strategy_engine/vwap_reversion_v2.py::_build_trade_levels",
            ),
        )
    return failures + _collect_repo_failures(data)


def _print_attempt_history(history: tuple[V2AttemptRecord, ...]) -> None:
    print()
    print("[STAGE B ATTEMPTS]")
    for attempt in history:
        print(
            f"{attempt.label}: min_deviation={attempt.parameters.min_deviation}, "
            f"rsi_long_threshold={attempt.parameters.rsi_long_threshold}, "
            f"rsi_short_threshold={attempt.parameters.rsi_short_threshold}, "
            f"volume_confirmation_multiplier={attempt.parameters.volume_confirmation_multiplier}, "
            f"trades={attempt.summary.trades}, "
            f"win_rate={_format_decimal(attempt.summary.win_rate)}, "
            f"profit_factor={_format_decimal(attempt.summary.profit_factor)}",
        )


def _print_failures(failures: list[ValidationFailure]) -> None:
    for failure in failures:
        print()
        print(f"[STAGE B FAILURE] {failure.gate}")
        print(f"Actual: {failure.actual}")
        print(f"Expected: {failure.expected}")
        print(f"Likely cause: {failure.likely_cause}")


async def main() -> None:
    _configure_logging()
    factory_passes_min_rrr, strategy_validates_rrr, pytest_bad_rrr_test_passed = await _b1_checks()
    v1_summary = await _run_stateful_backtest(
        cast("VWAPReversionStrategy", build_scalping_strategy(strategy_name="vwap_reversion", min_rrr=1.3)),
    )
    v2_summary, v2_parameters, attempt_history = await _run_v2_attempts()
    repo_checks = _build_repo_check_summary()

    validation_data = StageBValidationData(
        factory_passes_min_rrr=factory_passes_min_rrr,
        strategy_validates_rrr=strategy_validates_rrr,
        pytest_bad_rrr_test_passed=pytest_bad_rrr_test_passed,
        v1_summary=v1_summary,
        v2_summary=v2_summary,
        v2_parameters=v2_parameters,
        repo_checks=repo_checks,
        attempt_history=attempt_history,
    )
    _print_validation_block(validation_data)

    failures = _collect_failures(validation_data)
    if failures:
        _print_attempt_history(attempt_history)
        _print_failures(failures)
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
