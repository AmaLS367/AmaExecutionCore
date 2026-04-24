from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

SnapshotT = TypeVar("SnapshotT")


@dataclass(slots=True, frozen=True)
class MarketCandle:
    opened_at: datetime
    high: float
    low: float
    close: float
    open: float | None = None
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.open is None:
            object.__setattr__(self, "open", self.close)


@dataclass(slots=True, frozen=True)
class MarketSnapshotRequest:
    symbol: str
    interval: str
    limit: int


@dataclass(slots=True, frozen=True)
class MarketSnapshot:
    symbol: str
    interval: str
    candles: tuple[MarketCandle, ...]

    @property
    def last_price(self) -> float:
        return self.candles[-1].close

    @property
    def closes(self) -> tuple[float, ...]:
        return tuple(candle.close for candle in self.candles)

    @property
    def highs(self) -> tuple[float, ...]:
        return tuple(candle.high for candle in self.candles)

    @property
    def lows(self) -> tuple[float, ...]:
        return tuple(candle.low for candle in self.candles)

    @property
    def volumes(self) -> tuple[float, ...]:
        return tuple(candle.volume for candle in self.candles)


class MarketSnapshotProvider(ABC, Generic[SnapshotT]):
    @abstractmethod
    async def get_snapshot(self, request: MarketSnapshotRequest) -> SnapshotT:
        raise NotImplementedError
