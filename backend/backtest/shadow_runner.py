from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeVar, cast

from backend.market_data.contracts import MarketSnapshot
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.strategy_engine.contracts import StrategySignal
from backend.strategy_engine.service import StrategyExecutionRequest, StrategyExecutionResult

ExecutionResultT = TypeVar("ExecutionResultT")
ExecutionResultT_co = TypeVar("ExecutionResultT_co", covariant=True)


class SupportsStrategyExecutionService(Protocol):
    async def run(
        self,
        request: StrategyExecutionRequest,
    ) -> StrategyExecutionResult[MarketSnapshot]:
        ...


class SupportsExecutionService(Protocol[ExecutionResultT_co]):
    async def execute_signal(self, *, signal: ExecuteSignalRequest) -> ExecutionResultT_co:
        ...


@dataclass(slots=True, frozen=True)
class ShadowRunRequest:
    symbol: str
    interval: str


@dataclass(slots=True, frozen=True)
class ShadowRunResult(Generic[ExecutionResultT]):
    request: ShadowRunRequest
    snapshot: MarketSnapshot
    signal: StrategySignal | None
    execution: ExecutionResultT | None


class ShadowRunner(Generic[ExecutionResultT]):
    def __init__(
        self,
        *,
        strategy_execution_service: SupportsStrategyExecutionService,
        execution_service: SupportsExecutionService[ExecutionResultT],
    ) -> None:
        self._strategy_execution_service = strategy_execution_service
        self._execution_service = execution_service

    async def run_once(self, request: ShadowRunRequest) -> ShadowRunResult[ExecutionResultT]:
        strategy_result = await self._strategy_execution_service.run(
            StrategyExecutionRequest(symbol=request.symbol, interval=request.interval),
        )
        signal = strategy_result.signal
        if signal is None:
            return ShadowRunResult(
                request=request,
                snapshot=strategy_result.snapshot,
                signal=None,
                execution=None,
            )

        execution_result = await self._execution_service.execute_signal(
            signal=_to_execute_signal_request(signal),
        )
        return ShadowRunResult(
            request=request,
            snapshot=strategy_result.snapshot,
            signal=signal,
            execution=execution_result,
        )


def _to_execute_signal_request(signal: StrategySignal) -> ExecuteSignalRequest:
    if signal.direction not in ("long", "short"):
        raise ValueError(f"Unsupported strategy signal direction: {signal.direction}")
    direction = cast("Literal['long', 'short']", signal.direction)
    return ExecuteSignalRequest(
        symbol=signal.symbol,
        direction=direction,
        entry=signal.entry,
        stop=signal.stop,
        target=signal.target,
        reason=signal.reason,
        strategy_version=signal.strategy_version,
        indicators_snapshot=signal.indicators_snapshot,
    )
