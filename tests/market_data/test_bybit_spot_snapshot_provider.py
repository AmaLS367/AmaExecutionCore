from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.bybit_client.rest import BybitKline
from backend.market_data.bybit_spot import BybitSpotSnapshotProvider
from backend.market_data.contracts import MarketSnapshotRequest


class RecordingKlineClient:
    def __init__(self, klines: list[BybitKline] | None = None) -> None:
        self.calls: list[tuple[str, str, int, str, int | None]] = []
        self.klines = klines if klines is not None else [
            BybitKline(
                start_time=datetime(2024, 1, 1, 0, 1, tzinfo=UTC),
                open_price=101.0,
                high_price=111.0,
                low_price=96.0,
                close_price=109.0,
                volume=14.0,
                turnover=1400.0,
            ),
            BybitKline(
                start_time=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
                open_price=100.0,
                high_price=110.0,
                low_price=95.0,
                close_price=101.0,
                volume=12.0,
                turnover=1200.0,
            ),
        ]

    def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int,
        category: str = "spot",
        end: int | None = None,
    ) -> list[BybitKline]:
        self.calls.append((symbol, interval, limit, category, end))
        return list(self.klines)


@pytest.mark.asyncio
async def test_bybit_spot_snapshot_provider_normalizes_klines() -> None:
    client = RecordingKlineClient()
    provider = BybitSpotSnapshotProvider(rest_client=client)

    snapshot = await provider.get_snapshot(
        MarketSnapshotRequest(symbol="BTCUSDT", interval="1", limit=2),
    )

    assert client.calls == [("BTCUSDT", "1", 2, "spot", None)]
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.interval == "1"
    assert snapshot.last_price == 109.0
    assert [candle.close for candle in snapshot.candles] == [101.0, 109.0]
    assert [candle.high for candle in snapshot.candles] == [110.0, 111.0]
    assert [candle.low for candle in snapshot.candles] == [95.0, 96.0]
    assert [candle.volume for candle in snapshot.candles] == [12.0, 14.0]
    assert snapshot.volumes == (12.0, 14.0)


@pytest.mark.asyncio
async def test_bybit_spot_snapshot_provider_rejects_non_positive_limit() -> None:
    provider = BybitSpotSnapshotProvider(rest_client=RecordingKlineClient())

    with pytest.raises(ValueError, match="limit must be positive"):
        await provider.get_snapshot(MarketSnapshotRequest(symbol="BTCUSDT", interval="1", limit=0))


@pytest.mark.asyncio
async def test_bybit_spot_snapshot_provider_requires_enough_klines() -> None:
    provider = BybitSpotSnapshotProvider(rest_client=RecordingKlineClient(klines=[]))

    with pytest.raises(ValueError, match="expected 2"):
        await provider.get_snapshot(MarketSnapshotRequest(symbol="BTCUSDT", interval="1", limit=2))


class PaginatedKlineClient:
    def __init__(self) -> None:
        opened_at = datetime(2024, 1, 1, tzinfo=UTC)
        self.klines = [
            BybitKline(
                start_time=opened_at + timedelta(minutes=index),
                open_price=100.0 + index,
                high_price=101.0 + index,
                low_price=99.0 + index,
                close_price=100.0 + index,
                volume=1000.0 + index,
                turnover=10000.0 + index,
            )
            for index in range(1001)
        ]
        self.calls: list[tuple[int, int | None]] = []

    def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int,
        category: str = "spot",
        end: int | None = None,
    ) -> list[BybitKline]:
        del symbol, interval, category
        self.calls.append((limit, end))
        eligible = self.klines
        if end is not None:
            eligible = [
                kline
                for kline in self.klines
                if int(kline.start_time.timestamp() * 1000) <= end
            ]
        return list(reversed(eligible[-limit:]))


@pytest.mark.asyncio
async def test_bybit_spot_snapshot_provider_paginates_large_requests() -> None:
    client = PaginatedKlineClient()
    provider = BybitSpotSnapshotProvider(rest_client=client)

    snapshot = await provider.get_snapshot(
        MarketSnapshotRequest(symbol="BTCUSDT", interval="1", limit=1001),
    )

    assert [limit for limit, _ in client.calls] == [1000, 1]
    assert client.calls[1][1] is not None
    assert len(snapshot.candles) == 1001
    assert snapshot.candles[0].opened_at == client.klines[0].start_time
    assert snapshot.candles[-1].opened_at == client.klines[-1].start_time
