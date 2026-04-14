from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.market_data.bybit_spot import BybitSpotSnapshotProvider
from backend.market_data.contracts import MarketSnapshotRequest
from backend.bybit_client.rest import BybitKline


class RecordingKlineClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, str]] = []

    def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int,
        category: str = "spot",
        end: int | None = None,
    ) -> list[BybitKline]:
        self.calls.append((symbol, interval, limit, category))
        return [
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


@pytest.mark.asyncio
async def test_bybit_spot_snapshot_provider_normalizes_klines() -> None:
    provider = BybitSpotSnapshotProvider(rest_client=RecordingKlineClient())

    snapshot = await provider.get_snapshot(
        MarketSnapshotRequest(symbol="BTCUSDT", interval="1", limit=2)
    )

    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.interval == "1"
    assert snapshot.last_price == 109.0
    assert [candle.close for candle in snapshot.candles] == [101.0, 109.0]
    assert [candle.high for candle in snapshot.candles] == [110.0, 111.0]
    assert [candle.low for candle in snapshot.candles] == [95.0, 96.0]
    assert [candle.volume for candle in snapshot.candles] == [12.0, 14.0]
    assert snapshot.volumes == (12.0, 14.0)
