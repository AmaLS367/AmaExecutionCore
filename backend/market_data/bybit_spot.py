from __future__ import annotations

from typing import Protocol

from backend.bybit_client.rest import BybitKline
from backend.market_data.contracts import (
    MarketCandle,
    MarketSnapshot,
    MarketSnapshotProvider,
    MarketSnapshotRequest,
)


class SupportsBybitSpotKlines(Protocol):
    def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int,
        category: str = "spot",
        end: int | None = None,
    ) -> list[BybitKline]:
        ...


class BybitSpotSnapshotProvider(MarketSnapshotProvider[MarketSnapshot]):
    def __init__(self, *, rest_client: SupportsBybitSpotKlines) -> None:
        self._rest_client = rest_client

    async def get_snapshot(self, request: MarketSnapshotRequest) -> MarketSnapshot:
        if request.limit <= 0:
            raise ValueError("Snapshot request limit must be positive.")

        klines = self._rest_client.get_klines(
            symbol=request.symbol,
            interval=request.interval,
            limit=request.limit,
            category="spot",
        )
        if len(klines) < request.limit:
            raise ValueError(
                f"Bybit returned {len(klines)} candles for {request.symbol}; expected {request.limit}."
            )

        ordered_klines = sorted(klines, key=lambda candle: candle.start_time)
        candles = tuple(
            MarketCandle(
                opened_at=kline.start_time,
                high=kline.high_price,
                low=kline.low_price,
                close=kline.close_price,
                volume=kline.volume,
            )
            for kline in ordered_klines
        )
        return MarketSnapshot(
            symbol=request.symbol,
            interval=request.interval,
            candles=candles,
        )
