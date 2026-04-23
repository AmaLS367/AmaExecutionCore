from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from backend.market_data.contracts import MarketSnapshotProvider, MarketSnapshotRequest
from backend.strategy_engine.contracts import StrategySignal

SnapshotT = TypeVar("SnapshotT")
SnapshotT_contra = TypeVar("SnapshotT_contra", contravariant=True)


class SupportsStrategyExecution(Protocol[SnapshotT_contra]):
    @property
    def required_candle_count(self) -> int:
        ...

    async def generate_signal(self, snapshot: SnapshotT_contra) -> StrategySignal | None:
        ...


@dataclass(slots=True, frozen=True)
class StrategyExecutionRequest:
    symbol: str
    interval: str


@dataclass(slots=True, frozen=True)
class StrategyExecutionResult(Generic[SnapshotT]):
    request: StrategyExecutionRequest
    snapshot: SnapshotT
    signal: StrategySignal | None


class StrategyExecutionService(Generic[SnapshotT]):
    def __init__(
        self,
        *,
        snapshot_provider: MarketSnapshotProvider[SnapshotT],
        strategy: SupportsStrategyExecution[SnapshotT],
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self._strategy = strategy

    async def run(self, request: StrategyExecutionRequest) -> StrategyExecutionResult[SnapshotT]:
        normalized_request = _normalize_request(request)
        snapshot = await self._snapshot_provider.get_snapshot(
            MarketSnapshotRequest(
                symbol=normalized_request.symbol,
                interval=normalized_request.interval,
                limit=self._strategy.required_candle_count,
            ),
        )
        signal = await self._strategy.generate_signal(snapshot)
        return StrategyExecutionResult(
            request=normalized_request,
            snapshot=snapshot,
            signal=signal,
        )


def _normalize_request(request: StrategyExecutionRequest) -> StrategyExecutionRequest:
    symbol = request.symbol.strip().upper()
    interval = request.interval.strip()
    if not symbol:
        raise ValueError("Strategy execution symbol must not be empty.")
    if not interval:
        raise ValueError("Strategy execution interval must not be empty.")
    return StrategyExecutionRequest(symbol=symbol, interval=interval)
