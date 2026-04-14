from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

SnapshotT = TypeVar("SnapshotT")


@dataclass(slots=True)
class StrategySignal:
    symbol: str
    direction: str
    entry: float
    stop: float
    target: float
    reason: str | None = None
    strategy_version: str | None = None
    indicators_snapshot: dict[str, object] | None = None


class BaseStrategy(ABC, Generic[SnapshotT]):
    @property
    @abstractmethod
    def required_candle_count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def generate_signal(self, snapshot: SnapshotT) -> StrategySignal | None:
        raise NotImplementedError
