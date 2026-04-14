from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Protocol, TypeVar

from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.strategy_engine.contracts import StrategySignal

from backend.backtest.shadow_runner import SupportsExecutionService, _to_execute_signal_request

ExecutionResultT = TypeVar("ExecutionResultT")


class SupportsReplayStrategy(Protocol):
    @property
    def required_candle_count(self) -> int:
        ...

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        ...


@dataclass(slots=True, frozen=True)
class HistoricalReplayRequest:
    symbol: str
    interval: str
    candles: tuple[MarketCandle, ...] | None = None
    snapshots: tuple[MarketSnapshot, ...] | None = None
    start_step: int | None = None
    end_step: int | None = None


@dataclass(slots=True, frozen=True)
class HistoricalReplayStep(Generic[ExecutionResultT]):
    step_index: int
    snapshot: MarketSnapshot
    signal: StrategySignal | None
    execution: ExecutionResultT | None


@dataclass(slots=True, frozen=True)
class HistoricalReplayResult(Generic[ExecutionResultT]):
    request: HistoricalReplayRequest
    steps: tuple[HistoricalReplayStep[ExecutionResultT], ...]
    report: "HistoricalReplayReport"


@dataclass(slots=True, frozen=True)
class HistoricalReplayMetrics:
    closed_trades: int
    winning_trades: int
    losing_trades: int
    expectancy: Decimal | None
    win_rate: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal | None


@dataclass(slots=True, frozen=True)
class HistoricalReplaySlippageSummary:
    count: int
    average: Decimal
    minimum: Decimal
    maximum: Decimal


@dataclass(slots=True, frozen=True)
class HistoricalReplayReport:
    metrics: HistoricalReplayMetrics
    slippage: HistoricalReplaySlippageSummary | None


class HistoricalReplayRunner(Generic[ExecutionResultT]):
    def __init__(
        self,
        *,
        strategy: SupportsReplayStrategy,
        execution_service: SupportsExecutionService[ExecutionResultT],
    ) -> None:
        self._strategy = strategy
        self._execution_service = execution_service

    async def replay(
        self,
        request: HistoricalReplayRequest,
    ) -> HistoricalReplayResult[ExecutionResultT]:
        normalized_request = _normalize_request(request)
        replay_steps = _build_replay_steps(
            normalized_request,
            required_candle_count=self._strategy.required_candle_count,
        )

        results: list[HistoricalReplayStep[ExecutionResultT]] = []
        for step_index, snapshot in replay_steps:
            signal = await self._strategy.generate_signal(snapshot)
            execution: ExecutionResultT | None = None
            if signal is not None:
                execution = await self._execution_service.execute_signal(
                    signal=_to_execute_signal_request(signal)
                )
            results.append(
                HistoricalReplayStep(
                    step_index=step_index,
                    snapshot=snapshot,
                    signal=signal,
                    execution=execution,
                )
            )

        return HistoricalReplayResult(
            request=normalized_request,
            steps=tuple(results),
            report=_build_report(results),
        )


def _normalize_request(request: HistoricalReplayRequest) -> HistoricalReplayRequest:
    symbol = request.symbol.strip().upper()
    interval = request.interval.strip()
    if not symbol:
        raise ValueError("Replay symbol must not be empty.")
    if not interval:
        raise ValueError("Replay interval must not be empty.")
    if (request.candles is None) == (request.snapshots is None):
        raise ValueError("Replay request must provide exactly one of candles or snapshots.")
    if request.start_step is not None and request.start_step < 0:
        raise ValueError("Replay start_step must be greater than or equal to zero.")
    if request.end_step is not None and request.end_step < 0:
        raise ValueError("Replay end_step must be greater than or equal to zero.")
    return HistoricalReplayRequest(
        symbol=symbol,
        interval=interval,
        candles=request.candles,
        snapshots=request.snapshots,
        start_step=request.start_step,
        end_step=request.end_step,
    )


def _build_replay_steps(
    request: HistoricalReplayRequest,
    *,
    required_candle_count: int,
) -> list[tuple[int, MarketSnapshot]]:
    if request.snapshots is not None:
        return _build_snapshot_replay_steps(request)
    assert request.candles is not None
    return _build_candle_replay_steps(request, required_candle_count=required_candle_count)


def _build_snapshot_replay_steps(
    request: HistoricalReplayRequest,
) -> list[tuple[int, MarketSnapshot]]:
    assert request.snapshots is not None
    start_step = request.start_step or 0
    end_step = request.end_step if request.end_step is not None else len(request.snapshots)
    if end_step < start_step:
        raise ValueError("Replay end_step must be greater than or equal to start_step.")
    if end_step > len(request.snapshots):
        raise ValueError("Replay end_step exceeds available snapshots.")
    return [
        (step_index, request.snapshots[step_index])
        for step_index in range(start_step, end_step)
    ]


def _build_candle_replay_steps(
    request: HistoricalReplayRequest,
    *,
    required_candle_count: int,
) -> list[tuple[int, MarketSnapshot]]:
    assert request.candles is not None
    if len(request.candles) < required_candle_count:
        raise ValueError("Replay candles do not satisfy the strategy candle requirement.")

    minimum_step = required_candle_count - 1
    start_step = request.start_step if request.start_step is not None else minimum_step
    end_step = request.end_step if request.end_step is not None else len(request.candles)
    if start_step < minimum_step:
        raise ValueError("Replay start_step is earlier than the first valid candle window.")
    if end_step < start_step:
        raise ValueError("Replay end_step must be greater than or equal to start_step.")
    if end_step > len(request.candles):
        raise ValueError("Replay end_step exceeds available candles.")

    snapshots: list[tuple[int, MarketSnapshot]] = []
    for step_index in range(start_step, end_step):
        window_start = step_index - required_candle_count + 1
        snapshot = MarketSnapshot(
            symbol=request.symbol,
            interval=request.interval,
            candles=request.candles[window_start : step_index + 1],
        )
        snapshots.append((step_index, snapshot))
    return snapshots


def _build_report(
    steps: list[HistoricalReplayStep[ExecutionResultT]],
) -> HistoricalReplayReport:
    realized_pnls: list[Decimal] = []
    slippages: list[Decimal] = []
    for step in steps:
        if step.execution is None:
            continue
        realized_pnl = _coerce_decimal(_read_metric(step.execution, "realized_pnl"))
        if realized_pnl is not None:
            realized_pnls.append(realized_pnl)
        slippage = _coerce_decimal(_read_metric(step.execution, "slippage"))
        if slippage is not None:
            slippages.append(slippage)

    winning_trades = [pnl for pnl in realized_pnls if pnl > 0]
    losing_trades = [pnl for pnl in realized_pnls if pnl < 0]
    trade_count = len(realized_pnls)
    expectancy = sum(realized_pnls, Decimal("0")) / Decimal(trade_count) if trade_count else None
    win_rate = Decimal(len(winning_trades)) / Decimal(trade_count) if trade_count else None
    profit_factor: Decimal | None = None
    if losing_trades:
        profit_factor = sum(winning_trades, Decimal("0")) / abs(sum(losing_trades, Decimal("0")))
    max_drawdown = _calculate_max_drawdown(realized_pnls) if trade_count else None

    slippage_summary: HistoricalReplaySlippageSummary | None = None
    if slippages:
        slippage_summary = HistoricalReplaySlippageSummary(
            count=len(slippages),
            average=sum(slippages, Decimal("0")) / Decimal(len(slippages)),
            minimum=min(slippages),
            maximum=max(slippages),
        )

    return HistoricalReplayReport(
        metrics=HistoricalReplayMetrics(
            closed_trades=trade_count,
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            expectancy=expectancy,
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
        ),
        slippage=slippage_summary,
    )


def _read_metric(execution: object, field_name: str) -> object | None:
    if isinstance(execution, Mapping):
        return execution.get(field_name)
    return getattr(execution, field_name, None)


def _coerce_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        return Decimal(value)
    return None


def _calculate_max_drawdown(realized_pnls: list[Decimal]) -> Decimal:
    equity_curve = Decimal("0")
    peak_equity = Decimal("0")
    max_drawdown = Decimal("0")
    for realized_pnl in realized_pnls:
        equity_curve += realized_pnl
        if equity_curve > peak_equity:
            peak_equity = equity_curve
        drawdown = peak_equity - equity_curve
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return max_drawdown
