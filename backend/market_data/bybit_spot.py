from __future__ import annotations

from typing import Protocol

from backend.bybit_client.rest import BybitKline
from backend.market_data.contracts import (
    MarketCandle,
    MarketSnapshot,
    MarketSnapshotProvider,
    MarketSnapshotRequest,
)

_MAX_KLINE_BATCH_SIZE = 1000


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

        klines: list[BybitKline] = []
        end_cursor: int | None = None
        while len(klines) < request.limit:
            batch_limit = min(_MAX_KLINE_BATCH_SIZE, request.limit - len(klines))
            batch = self._rest_client.get_klines(
                symbol=request.symbol,
                interval=request.interval,
                limit=batch_limit,
                category="spot",
                end=end_cursor,
            )
            if not batch:
                break
            ordered_batch = sorted(batch, key=lambda candle: candle.start_time)
            klines.extend(ordered_batch)
            oldest_candle = ordered_batch[0]
            end_cursor = int(oldest_candle.start_time.timestamp() * 1000) - 1
            if len(batch) < batch_limit:
                break

        deduped_klines = {kline.start_time: kline for kline in klines}
        if len(deduped_klines) < request.limit:
            raise ValueError(
                f"Bybit returned {len(deduped_klines)} candles for {request.symbol}; "
                f"expected {request.limit}.",
            )

        ordered_klines = sorted(deduped_klines.values(), key=lambda candle: candle.start_time)[
            -request.limit :
        ]
        candles = tuple(
            MarketCandle(
                opened_at=kline.start_time,
                open=kline.open_price,
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
