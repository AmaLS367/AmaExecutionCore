from __future__ import annotations

from typing import Generic, Protocol, TypeVar

from backend.strategy_engine.contracts import BaseStrategy, StrategySignal

SnapshotT = TypeVar("SnapshotT")
SnapshotT_contra = TypeVar("SnapshotT_contra", contravariant=True)


class SupportsOrchestratedStrategy(Protocol[SnapshotT_contra]):
    @property
    def required_candle_count(self) -> int:
        ...

    async def generate_signal(self, snapshot: SnapshotT_contra) -> StrategySignal | None:
        ...


class StrategyOrchestrator(BaseStrategy[SnapshotT], Generic[SnapshotT]):
    def __init__(self, *, strategies: tuple[SupportsOrchestratedStrategy[SnapshotT], ...]) -> None:
        if not strategies:
            raise ValueError("StrategyOrchestrator requires at least one strategy.")
        self._strategies = strategies

    @property
    def required_candle_count(self) -> int:
        return max(strategy.required_candle_count for strategy in self._strategies)

    async def generate_signal(self, snapshot: SnapshotT) -> StrategySignal | None:
        for strategy in self._strategies:
            signal = await strategy.generate_signal(snapshot)
            if signal is not None:
                return signal
        return None
