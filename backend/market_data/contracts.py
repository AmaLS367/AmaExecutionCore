from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

SnapshotT = TypeVar("SnapshotT")


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    last_price: float


class MarketSnapshotProvider(ABC, Generic[SnapshotT]):
    @abstractmethod
    async def get_snapshot(self, symbol: str) -> SnapshotT:
        raise NotImplementedError
