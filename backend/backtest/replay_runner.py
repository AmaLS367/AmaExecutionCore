from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Protocol, TypeGuard, TypeVar, cast

from backend.backtest.metrics import calculate_max_drawdown
from backend.backtest.shadow_runner import SupportsExecutionService, _to_execute_signal_request
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.strategy_engine.contracts import StrategySignal

ExecutionResultT = TypeVar("ExecutionResultT")
ExecutionResultT_co = TypeVar("ExecutionResultT_co", covariant=True)


class SupportsReplayStrategy(Protocol):
    @property
    def required_candle_count(self) -> int:
        ...

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        ...


class SupportsReplayExecutionContext(Protocol[ExecutionResultT_co]):
    async def execute_replay_signal(
        self,
        *,
        signal: ExecuteSignalRequest,
        future_candles: tuple[MarketCandle, ...],
        step_index: int,
    ) -> ExecutionResultT:
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
    report: HistoricalReplayReport


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
class HistoricalReplayCounters:
    rejected_short_signals: int = 0
    skipped_min_notional: int = 0
    skipped_insufficient_capital: int = 0
    ambiguous_candles: int = 0


@dataclass(slots=True, frozen=True)
class HistoricalReplayReport:
    metrics: HistoricalReplayMetrics
    slippage: HistoricalReplaySlippageSummary | None
    counters: HistoricalReplayCounters


@dataclass(slots=True, frozen=True)
class ReplayOpenPosition:
    symbol: str
    direction: str
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    opened_at_step: int
    planned_close_step: int


@dataclass(slots=True)
class ReplayPortfolioState:
    open_positions: dict[str, ReplayOpenPosition]
    cooldown_until: dict[str, int]
    daily_trades: dict[str, int]
    consecutive_losses: int
    session_halted: bool
    current_date_str: str | None


@dataclass(slots=True, frozen=True)
class ReplayScheduledClosure:
    symbol: str
    close_step: int
    entry_date_str: str
    net_trade_pnl: Decimal | None


class HistoricalReplayRunner(Generic[ExecutionResultT]):
    def __init__(
        self,
        *,
        strategy: SupportsReplayStrategy,
        execution_service: SupportsExecutionService[ExecutionResultT]
        | SupportsReplayExecutionContext[ExecutionResultT],
        max_open_positions: int = 1,
        max_trades_per_day: int = 10,
        cooldown_candles: int = 2,
        hard_pause_consecutive_losses: int = 5,
    ) -> None:
        self._strategy = strategy
        self._execution_service = execution_service
        self._max_open_positions = max_open_positions
        self._max_trades_per_day = max_trades_per_day
        self._cooldown_candles = cooldown_candles
        self._hard_pause_consecutive_losses = hard_pause_consecutive_losses

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
        portfolio_state = ReplayPortfolioState(
            open_positions={},
            cooldown_until={},
            daily_trades={},
            consecutive_losses=0,
            session_halted=False,
            current_date_str=None,
        )
        scheduled_closures: dict[int, list[ReplayScheduledClosure]] = {}
        for step_index, snapshot in replay_steps:
            current_date_str = snapshot.candles[-1].opened_at.date().isoformat()
            _reset_daily_circuit_breaker(
                state=portfolio_state,
                current_date_str=current_date_str,
            )
            _apply_scheduled_closures(
                state=portfolio_state,
                scheduled_closures=scheduled_closures,
                step_index=step_index,
                cooldown_candles=self._cooldown_candles,
                hard_pause_consecutive_losses=self._hard_pause_consecutive_losses,
            )
            signal = await self._strategy.generate_signal(snapshot)
            execution: ExecutionResultT | None = None
            if signal is not None and _can_execute_signal(
                state=portfolio_state,
                signal=signal,
                step_index=step_index,
                date_str=current_date_str,
                max_open_positions=self._max_open_positions,
                max_trades_per_day=self._max_trades_per_day,
            ):
                execute_signal_request = _to_execute_signal_request(signal)
                if _supports_replay_execution_context(self._execution_service):
                    future_candles = _future_candles_for_step(
                        normalized_request,
                        step_index=step_index,
                    )
                    execution = await self._execution_service.execute_replay_signal(
                        signal=execute_signal_request,
                        future_candles=future_candles,
                        step_index=step_index,
                    )
                else:
                    execution_service = self._execution_service
                    execution = await cast(
                        "SupportsExecutionService[ExecutionResultT]",
                        execution_service,
                    ).execute_signal(
                        signal=execute_signal_request,
                    )
                _track_execution(
                    state=portfolio_state,
                    scheduled_closures=scheduled_closures,
                    signal=signal,
                    execution=execution,
                    step_index=step_index,
                    entry_date_str=current_date_str,
                    cooldown_candles=self._cooldown_candles,
                    hard_pause_consecutive_losses=self._hard_pause_consecutive_losses,
                )
            results.append(
                HistoricalReplayStep(
                    step_index=step_index,
                    snapshot=snapshot,
                    signal=signal,
                    execution=execution,
                ),
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
    counters = _collect_report_counters(steps)
    realized_pnls, slippages = _collect_trade_outputs(steps)

    winning_trades = [pnl for pnl in realized_pnls if pnl > 0]
    losing_trades = [pnl for pnl in realized_pnls if pnl < 0]
    trade_count = len(realized_pnls)
    expectancy = sum(realized_pnls, Decimal(0)) / Decimal(trade_count) if trade_count else None
    win_rate = Decimal(len(winning_trades)) / Decimal(trade_count) if trade_count else None
    profit_factor: Decimal | None = None
    if losing_trades:
        profit_factor = sum(winning_trades, Decimal(0)) / abs(sum(losing_trades, Decimal(0)))
    max_drawdown = calculate_max_drawdown(realized_pnls) if trade_count else None

    slippage_summary: HistoricalReplaySlippageSummary | None = None
    if slippages:
        slippage_summary = HistoricalReplaySlippageSummary(
            count=len(slippages),
            average=sum(slippages, Decimal(0)) / Decimal(len(slippages)),
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
        counters=counters,
    )


def _collect_report_counters(
    steps: list[HistoricalReplayStep[ExecutionResultT]],
) -> HistoricalReplayCounters:
    rejected_short_signals = 0
    skipped_min_notional = 0
    skipped_insufficient_capital = 0
    ambiguous_candles = 0
    for step in steps:
        if step.execution is None:
            continue
        if _read_bool(step.execution, "rejected_short_signal"):
            rejected_short_signals += 1
        if _read_bool(step.execution, "skipped_min_notional"):
            skipped_min_notional += 1
        if _read_bool(step.execution, "skipped_insufficient_capital"):
            skipped_insufficient_capital += 1
        if _read_bool(step.execution, "ambiguous_candle"):
            ambiguous_candles += 1
    return HistoricalReplayCounters(
        rejected_short_signals=rejected_short_signals,
        skipped_min_notional=skipped_min_notional,
        skipped_insufficient_capital=skipped_insufficient_capital,
        ambiguous_candles=ambiguous_candles,
    )


def _collect_trade_outputs(
    steps: list[HistoricalReplayStep[ExecutionResultT]],
) -> tuple[list[Decimal], list[Decimal]]:
    realized_pnls: list[Decimal] = []
    slippages: list[Decimal] = []
    for step in steps:
        if step.execution is None or not _execution_was_executed(step.execution):
            continue
        realized_pnl = _coerce_decimal(_read_metric(step.execution, "realized_pnl"))
        if realized_pnl is not None:
            realized_pnls.append(realized_pnl)
        slippage = _coerce_decimal(_read_metric(step.execution, "slippage"))
        if slippage is not None:
            slippages.append(slippage)
    return realized_pnls, slippages


def _read_metric(execution: object, field_name: str) -> object | None:
    if isinstance(execution, Mapping):
        return execution.get(field_name)
    return getattr(execution, field_name, None)


def _read_bool(execution: object, field_name: str) -> bool:
    value = _read_metric(execution, field_name)
    return value is True


def _execution_was_executed(execution: object) -> bool:
    status = _read_metric(execution, "status")
    if isinstance(status, str):
        return status != "skipped"
    return True


def _track_execution(
    *,
    state: ReplayPortfolioState,
    scheduled_closures: dict[int, list[ReplayScheduledClosure]],
    signal: StrategySignal,
    execution: object,
    step_index: int,
    entry_date_str: str,
    cooldown_candles: int,
    hard_pause_consecutive_losses: int,
) -> None:
    if not _execution_was_executed(execution):
        return
    close_step = _resolve_close_step(execution=execution, step_index=step_index)
    state.open_positions[signal.symbol] = ReplayOpenPosition(
        symbol=signal.symbol,
        direction=signal.direction,
        entry_price=_coerce_decimal(_read_metric(execution, "entry_price")) or Decimal(str(signal.entry)),
        stop_price=Decimal(str(signal.stop)),
        target_price=Decimal(str(signal.target)),
        opened_at_step=step_index,
        planned_close_step=close_step,
    )
    closure = ReplayScheduledClosure(
        symbol=signal.symbol,
        close_step=close_step,
        entry_date_str=entry_date_str,
        net_trade_pnl=_net_trade_pnl(execution),
    )
    if close_step <= step_index:
        _record_closed_trade(
            state=state,
            closure=closure,
            cooldown_candles=cooldown_candles,
            hard_pause_consecutive_losses=hard_pause_consecutive_losses,
        )
        return
    scheduled_closures.setdefault(close_step, []).append(closure)


def _apply_scheduled_closures(
    *,
    state: ReplayPortfolioState,
    scheduled_closures: dict[int, list[ReplayScheduledClosure]],
    step_index: int,
    cooldown_candles: int,
    hard_pause_consecutive_losses: int,
) -> None:
    for closure in scheduled_closures.pop(step_index, []):
        _record_closed_trade(
            state=state,
            closure=closure,
            cooldown_candles=cooldown_candles,
            hard_pause_consecutive_losses=hard_pause_consecutive_losses,
        )


def _record_closed_trade(
    *,
    state: ReplayPortfolioState,
    closure: ReplayScheduledClosure,
    cooldown_candles: int,
    hard_pause_consecutive_losses: int,
) -> None:
    state.open_positions.pop(closure.symbol, None)
    state.cooldown_until[closure.symbol] = closure.close_step + cooldown_candles
    state.daily_trades[closure.entry_date_str] = state.daily_trades.get(closure.entry_date_str, 0) + 1

    if closure.net_trade_pnl is None:
        return
    if closure.net_trade_pnl < 0:
        state.consecutive_losses += 1
        if state.consecutive_losses >= hard_pause_consecutive_losses:
            state.session_halted = True
        return
    if closure.net_trade_pnl > 0:
        state.consecutive_losses = 0


def _reset_daily_circuit_breaker(
    *,
    state: ReplayPortfolioState,
    current_date_str: str,
) -> None:
    if state.current_date_str == current_date_str:
        return
    state.current_date_str = current_date_str
    state.consecutive_losses = 0
    state.session_halted = False


def _can_execute_signal(
    *,
    state: ReplayPortfolioState,
    signal: StrategySignal,
    step_index: int,
    date_str: str,
    max_open_positions: int,
    max_trades_per_day: int,
) -> bool:
    if state.session_halted:
        return False
    if signal.symbol in state.open_positions:
        return False
    if len(state.open_positions) >= max_open_positions:
        return False
    if step_index <= state.cooldown_until.get(signal.symbol, -1):
        return False
    return state.daily_trades.get(date_str, 0) < max_trades_per_day


def _resolve_close_step(*, execution: object, step_index: int) -> int:
    explicit_close_step = _coerce_int(_read_metric(execution, "closed_at_step"))
    if explicit_close_step is not None:
        return explicit_close_step

    hold_candles = _coerce_int(_read_metric(execution, "hold_candles"))
    if hold_candles is None:
        return step_index
    return step_index + hold_candles


def _net_trade_pnl(execution: object) -> Decimal | None:
    realized_pnl = _coerce_decimal(_read_metric(execution, "realized_pnl"))
    fees_paid = _coerce_decimal(_read_metric(execution, "fees_paid"))
    if realized_pnl is None:
        return None
    if fees_paid is None:
        return realized_pnl
    return realized_pnl - fees_paid


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


def _coerce_int(value: object | None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _future_candles_for_step(
    request: HistoricalReplayRequest,
    *,
    step_index: int,
) -> tuple[MarketCandle, ...]:
    if request.candles is None:
        return ()
    return request.candles[step_index + 1 :]


def _supports_replay_execution_context(
    execution_service: object,
) -> TypeGuard[SupportsReplayExecutionContext[ExecutionResultT]]:
    return hasattr(execution_service, "execute_replay_signal")
