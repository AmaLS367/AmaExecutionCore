from __future__ import annotations

import asyncio
import os
import re
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from backend.backtest import (
    HistoricalReplayRequest,
    HistoricalReplayRunner,
    SimulationExecutionResult,
    SimulationExecutionService,
)
from backend.backtest.datasets import candles_for_lookback, load_dataset
from backend.backtest.replay_runner import SupportsReplayExecutionContext
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.strategy_engine.contracts import StrategySignal
from backend.strategy_engine.factory import build_scalping_strategy

DATASET_PATH = Path("scripts/fixtures/regression/btcusdt_5m_365d.json.gz")
RISK_AMOUNT_USD = 100.0


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
class BacktestSummary:
    trades: int
    net_pnl: Decimal
    average_fees_per_trade: Decimal
    executed_step_indexes: tuple[int, ...]
    fees_by_step_index: dict[int, Decimal]


@dataclass(slots=True, frozen=True)
class ValidationFailure:
    gate: str
    actual: str
    expected: str
    likely_cause: str


@dataclass(slots=True, frozen=True)
class BacktestAttempt:
    summary: BacktestSummary | None
    failure: ValidationFailure | None


@dataclass(slots=True, frozen=True)
class StageAValidationData:
    old_summary: BacktestSummary
    new_summary: BacktestSummary
    portfolio_checks: dict[str, bool]
    repo_checks: RepoCheckSummary


@dataclass(slots=True)
class PlannedReplayExecutionService:
    pnl_values: list[Decimal]
    close_step_offsets: list[int]
    index: int = 0

    async def execute_replay_signal(
        self,
        *,
        signal: ExecuteSignalRequest,
        future_candles: tuple[MarketCandle, ...],
        step_index: int,
    ) -> dict[str, object]:
        del future_candles
        pnl_value = self.pnl_values[self.index]
        close_step_offset = self.close_step_offsets[self.index]
        self.index += 1
        return {
            "symbol": signal.symbol,
            "realized_pnl": pnl_value,
            "fees_paid": Decimal(0),
            "closed_at_step": step_index + close_step_offset,
            "entry_price": Decimal(str(signal.entry)),
        }


class AlwaysSignalStrategy:
    required_candle_count = 1

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        return StrategySignal(
            symbol=snapshot.symbol,
            direction="long",
            entry=snapshot.last_price,
            stop=snapshot.last_price - 5.0,
            target=snapshot.last_price + 10.0,
            reason="always",
            strategy_version="always-v1",
        )


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
    return format(value, "f")


def _passfail(*, passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _load_last_90_days() -> tuple[MarketCandle, ...]:
    dataset = load_dataset(DATASET_PATH)
    candle_count = candles_for_lookback(interval=dataset.interval, lookback_days=90)
    return dataset.candles[-candle_count:]


def _build_execute_signal_request(signal: StrategySignal) -> ExecuteSignalRequest:
    return ExecuteSignalRequest(
        symbol=signal.symbol,
        direction=cast("Literal['long', 'short']", signal.direction),
        entry=signal.entry,
        stop=signal.stop,
        target=signal.target,
        reason=signal.reason,
        strategy_version=signal.strategy_version,
        indicators_snapshot=signal.indicators_snapshot,
    )


def _net_pnl_from_executions(executions: list[SimulationExecutionResult]) -> Decimal:
    return sum((execution.realized_pnl - execution.fees_paid for execution in executions), Decimal(0))


def _average_fees_per_trade(executions: list[SimulationExecutionResult]) -> Decimal:
    if not executions:
        return Decimal(0)
    return sum((execution.fees_paid for execution in executions), Decimal(0)) / Decimal(len(executions))


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


def _align_old_summary_to_new_executions(
    *,
    old_summary: BacktestSummary,
    new_summary: BacktestSummary,
) -> BacktestSummary:
    if not new_summary.executed_step_indexes:
        return old_summary
    matched_fees = [
        old_summary.fees_by_step_index[step_index]
        for step_index in new_summary.executed_step_indexes
        if step_index in old_summary.fees_by_step_index
    ]
    matched_average_fees = Decimal(0)
    if matched_fees:
        matched_average_fees = sum(matched_fees, Decimal(0)) / Decimal(len(matched_fees))
    return BacktestSummary(
        trades=old_summary.trades,
        net_pnl=old_summary.net_pnl,
        average_fees_per_trade=matched_average_fees,
        executed_step_indexes=new_summary.executed_step_indexes,
        fees_by_step_index=old_summary.fees_by_step_index,
    )


async def _run_stateless_backtest(candles: tuple[MarketCandle, ...]) -> BacktestSummary:
    strategy = build_scalping_strategy(strategy_name="vwap_reversion", min_rrr=1.5)
    execution_service = SimulationExecutionService(
        max_hold_candles=20,
        risk_amount_usd=RISK_AMOUNT_USD,
        fee_rate_per_side=0.001,
    )
    executions: list[SimulationExecutionResult] = []
    executed_step_indexes: list[int] = []
    fees_by_step_index: dict[int, Decimal] = {}
    start_step = strategy.required_candle_count - 1

    for step_index in range(start_step, len(candles)):
        window_start = step_index - strategy.required_candle_count + 1
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            interval="5",
            candles=candles[window_start : step_index + 1],
        )
        signal = await strategy.generate_signal(snapshot)
        if signal is None:
            continue
        execution = await execution_service.execute_replay_signal(
            signal=_build_execute_signal_request(signal),
            future_candles=candles[step_index + 1 :],
            step_index=step_index,
        )
        executions.append(execution)
        executed_step_indexes.append(step_index)
        fees_by_step_index[step_index] = execution.fees_paid

    return BacktestSummary(
        trades=len(executions),
        net_pnl=_net_pnl_from_executions(executions),
        average_fees_per_trade=_average_fees_per_trade(executions),
        executed_step_indexes=tuple(executed_step_indexes),
        fees_by_step_index=fees_by_step_index,
    )


async def _run_stateful_backtest(candles: tuple[MarketCandle, ...]) -> BacktestSummary:
    strategy = build_scalping_strategy(strategy_name="vwap_reversion", min_rrr=1.5)
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
    return BacktestSummary(
        trades=len(executions),
        net_pnl=_net_pnl_from_executions(executions),
        average_fees_per_trade=_average_fees_per_trade(executions),
        executed_step_indexes=tuple(
            step.step_index
            for step in result.steps
            if step.execution is not None
        ),
        fees_by_step_index={
            step.step_index: step.execution.fees_paid
            for step in result.steps
            if step.execution is not None
        },
    )


def _build_snapshots(symbols: list[str]) -> tuple[MarketSnapshot, ...]:
    first_opened_at = load_dataset(DATASET_PATH).candles[0].opened_at
    candles = tuple(
        MarketCandle(
            opened_at=first_opened_at,
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=10.0 + index,
        )
        for index in range(len(symbols))
    )
    return tuple(
        MarketSnapshot(symbol=symbol, interval="5", candles=(candles[index],))
        for index, symbol in enumerate(symbols)
    )


def _build_portfolio_runner(
    *,
    pnl_values: list[Decimal],
    close_step_offsets: list[int],
    max_open_positions: int = 1,
    max_trades_per_day: int = 10,
    cooldown_candles: int = 2,
    hard_pause_consecutive_losses: int = 5,
) -> HistoricalReplayRunner[dict[str, object]]:
    execution_service = cast(
        "SupportsReplayExecutionContext[dict[str, object]]",
        PlannedReplayExecutionService(
            pnl_values=pnl_values,
            close_step_offsets=close_step_offsets,
        ),
    )
    return HistoricalReplayRunner(
        strategy=AlwaysSignalStrategy(),
        execution_service=execution_service,
        max_open_positions=max_open_positions,
        max_trades_per_day=max_trades_per_day,
        cooldown_candles=cooldown_candles,
        hard_pause_consecutive_losses=hard_pause_consecutive_losses,
    )


async def _portfolio_enforcement_checks() -> dict[str, bool]:
    max_open_positions_result = await _build_portfolio_runner(
        pnl_values=[Decimal(10), Decimal(10)],
        close_step_offsets=[2, 0],
        max_open_positions=1,
        cooldown_candles=0,
    ).replay(
        HistoricalReplayRequest(
            symbol="MIXED",
            interval="5",
            snapshots=_build_snapshots(["BTCUSDT", "ETHUSDT", "XRPUSDT"]),
        ),
    )
    cooldown_result = await _build_portfolio_runner(
        pnl_values=[Decimal(10), Decimal(10)],
        close_step_offsets=[0, 0],
        cooldown_candles=1,
    ).replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="5",
            snapshots=_build_snapshots(["BTCUSDT", "BTCUSDT", "BTCUSDT"]),
        ),
    )
    daily_cap_result = await _build_portfolio_runner(
        pnl_values=[Decimal(10)],
        close_step_offsets=[0],
        cooldown_candles=0,
        max_trades_per_day=1,
    ).replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="5",
            snapshots=_build_snapshots(["BTCUSDT", "BTCUSDT", "BTCUSDT"]),
        ),
    )
    circuit_breaker_result = await _build_portfolio_runner(
        pnl_values=[Decimal(-10), Decimal(-10)],
        close_step_offsets=[0, 0],
        cooldown_candles=0,
        hard_pause_consecutive_losses=2,
    ).replay(
        HistoricalReplayRequest(
            symbol="BTCUSDT",
            interval="5",
            snapshots=_build_snapshots(["BTCUSDT", "BTCUSDT", "BTCUSDT"]),
        ),
    )
    return {
        "max_open_positions": (
            max_open_positions_result.steps[0].execution is not None
            and max_open_positions_result.steps[1].execution is None
            and max_open_positions_result.steps[2].execution is not None
        ),
        "cooldown": (
            cooldown_result.steps[0].execution is not None
            and cooldown_result.steps[1].execution is None
            and cooldown_result.steps[2].execution is not None
        ),
        "max_trades_per_day": (
            daily_cap_result.steps[0].execution is not None
            and daily_cap_result.steps[1].execution is None
            and daily_cap_result.steps[2].execution is None
        ),
        "circuit_breaker": (
            circuit_breaker_result.steps[0].execution is not None
            and circuit_breaker_result.steps[1].execution is not None
            and circuit_breaker_result.steps[2].execution is None
        ),
    }


async def _attempt_backtest(
    *,
    label: str,
    runner: Callable[[tuple[MarketCandle, ...]], Awaitable[BacktestSummary]],
    likely_cause: str,
) -> BacktestAttempt:
    try:
        summary = await runner(_load_last_90_days())
    except (ArithmeticError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return BacktestAttempt(
            summary=None,
            failure=ValidationFailure(
                gate=f"{label} exception",
                actual=str(exc),
                expected="No exception",
                likely_cause=likely_cause,
            ),
        )
    return BacktestAttempt(summary=summary, failure=None)


def _print_validation_block(data: StageAValidationData) -> None:
    trade_count_gate = data.new_summary.trades < data.old_summary.trades
    avg_fee_gate = data.new_summary.average_fees_per_trade >= data.old_summary.average_fees_per_trade
    stage_result = (
        trade_count_gate
        and avg_fee_gate
        and all(data.portfolio_checks.values())
        and data.repo_checks.pytest_result.passed
        and data.repo_checks.ruff_result.passed
        and data.repo_checks.mypy_result.passed
    )

    print("[STAGE A VALIDATION]")
    print(f"Old stateless trades:   {data.old_summary.trades}")
    print(f"New stateful trades:    {data.new_summary.trades}")
    print(f"Constraint: M < N -> {_passfail(passed=trade_count_gate)}")
    print()
    print(f"Old avg_fee_per_trade (flat fee):       {_format_decimal(data.old_summary.average_fees_per_trade)}")
    print(f"New avg_fee_per_trade (realistic cost): {_format_decimal(data.new_summary.average_fees_per_trade)}")
    print(f"Constraint: new >= old -> {_passfail(passed=avg_fee_gate)}")
    print()
    print("Portfolio state enforcement:")
    print(f"  max_open_positions:   {_passfail(passed=data.portfolio_checks['max_open_positions'])}")
    print(f"  cooldown:             {_passfail(passed=data.portfolio_checks['cooldown'])}")
    print(f"  max_trades_per_day:   {_passfail(passed=data.portfolio_checks['max_trades_per_day'])}")
    print(f"  circuit_breaker:      {_passfail(passed=data.portfolio_checks['circuit_breaker'])}")
    print()
    print(
        f"Pytest: {data.repo_checks.pytest_passed} passed, {data.repo_checks.pytest_failed} failed",
    )
    print(f"Ruff:   {_passfail(passed=data.repo_checks.ruff_result.passed)}")
    print(f"Mypy:   {_passfail(passed=data.repo_checks.mypy_result.passed)}")
    print()
    print(f"[STAGE A RESULT]: {_passfail(passed=stage_result)}")


def _collect_failures(
    *,
    data: StageAValidationData,
    attempts: list[BacktestAttempt],
) -> list[ValidationFailure]:
    failures = [attempt.failure for attempt in attempts if attempt.failure is not None]
    if data.new_summary.trades >= data.old_summary.trades:
        failures.append(
            ValidationFailure(
                gate="Constraint: M < N",
                actual=f"M={data.new_summary.trades}, N={data.old_summary.trades}",
                expected="M < N",
                likely_cause="backend/backtest/replay_runner.py::_can_execute_signal or backend/backtest/replay_runner.py::_track_execution",
            ),
        )
    if data.new_summary.average_fees_per_trade < data.old_summary.average_fees_per_trade:
        failures.append(
            ValidationFailure(
                gate="Constraint: new avg_fee_per_trade >= old avg_fee_per_trade",
                actual=(
                    f"new={_format_decimal(data.new_summary.average_fees_per_trade)}, "
                    f"old={_format_decimal(data.old_summary.average_fees_per_trade)}"
                ),
                expected="new >= old",
                likely_cause="backend/backtest/simulation_execution_service.py::execute_replay_signal",
            ),
        )

    portfolio_gate_specs = {
        "max_open_positions": (
            "Synthetic max-open-positions scenario did not block the second symbol while one trade was active.",
            "Second signal blocked until the first trade closes.",
            "backend/backtest/replay_runner.py::_can_execute_signal",
        ),
        "cooldown": (
            "Synthetic cooldown scenario allowed a trade inside the cooldown window.",
            "Trade skipped while step_index <= cooldown_until[symbol].",
            "backend/backtest/replay_runner.py::_record_closed_trade",
        ),
        "max_trades_per_day": (
            "Synthetic daily-cap scenario executed more than one trade on the same date.",
            "No trades after the configured daily limit is reached.",
            "backend/backtest/replay_runner.py::_can_execute_signal",
        ),
        "circuit_breaker": (
            "Synthetic loss-streak scenario kept trading after the configured hard pause threshold.",
            "Session halted after consecutive losses reach the threshold.",
            "backend/backtest/replay_runner.py::_record_closed_trade",
        ),
    }
    for gate_name, passed in data.portfolio_checks.items():
        if passed:
            continue
        actual, expected, likely_cause = portfolio_gate_specs[gate_name]
        failures.append(
            ValidationFailure(
                gate=f"Portfolio state enforcement: {gate_name}",
                actual=actual,
                expected=expected,
                likely_cause=likely_cause,
            ),
        )

    if not data.repo_checks.pytest_result.passed:
        failures.append(
            ValidationFailure(
                gate="Pytest",
                actual=f"{data.repo_checks.pytest_passed} passed, {data.repo_checks.pytest_failed} failed",
                expected="0 failed",
                likely_cause="tests/ or the Stage A code paths they exercise",
            ),
        )
    if not data.repo_checks.ruff_result.passed:
        failures.append(
            ValidationFailure(
                gate="Ruff",
                actual=data.repo_checks.ruff_result.stdout + data.repo_checks.ruff_result.stderr,
                expected="All checks passed",
                likely_cause="Recently edited Python files in Stage A",
            ),
        )
    if not data.repo_checks.mypy_result.passed:
        failures.append(
            ValidationFailure(
                gate="Mypy",
                actual=data.repo_checks.mypy_result.stdout + data.repo_checks.mypy_result.stderr,
                expected="Success: no issues found",
                likely_cause="Recently edited Python files in Stage A",
            ),
        )
    return failures


def _print_failures(failures: list[ValidationFailure]) -> None:
    for failure in failures:
        print()
        print(f"[STAGE A FAILURE] {failure.gate}")
        print(f"Actual: {failure.actual}")
        print(f"Expected: {failure.expected}")
        print(f"Likely cause: {failure.likely_cause}")


async def main() -> None:
    repo_checks = _build_repo_check_summary()
    portfolio_checks = await _portfolio_enforcement_checks()
    old_attempt = await _attempt_backtest(
        label="Old-style stateless replay",
        runner=_run_stateless_backtest,
        likely_cause="scripts/validate_stage_a.py::_run_stateless_backtest or backend/backtest/simulation_execution_service.py",
    )
    new_attempt = await _attempt_backtest(
        label="New-style stateful replay",
        runner=_run_stateful_backtest,
        likely_cause="backend/backtest/replay_runner.py::HistoricalReplayRunner.replay or backend/backtest/simulation_execution_service.py",
    )

    if old_attempt.summary is None or new_attempt.summary is None:
        failures = [attempt.failure for attempt in (old_attempt, new_attempt) if attempt.failure is not None]
        fallback_data = StageAValidationData(
            old_summary=old_attempt.summary
            or BacktestSummary(
                trades=0,
                net_pnl=Decimal(0),
                average_fees_per_trade=Decimal(0),
                executed_step_indexes=(),
                fees_by_step_index={},
            ),
            new_summary=new_attempt.summary
            or BacktestSummary(
                trades=0,
                net_pnl=Decimal(0),
                average_fees_per_trade=Decimal(0),
                executed_step_indexes=(),
                fees_by_step_index={},
            ),
            portfolio_checks=portfolio_checks,
            repo_checks=repo_checks,
        )
        _print_validation_block(fallback_data)
        _print_failures(failures)
        raise SystemExit(1)

    validation_data = StageAValidationData(
        old_summary=_align_old_summary_to_new_executions(
            old_summary=old_attempt.summary,
            new_summary=new_attempt.summary,
        ),
        new_summary=new_attempt.summary,
        portfolio_checks=portfolio_checks,
        repo_checks=repo_checks,
    )
    _print_validation_block(validation_data)

    failures = _collect_failures(
        data=validation_data,
        attempts=[old_attempt, new_attempt],
    )
    if failures:
        _print_failures(failures)
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
