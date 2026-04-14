from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.bybit_client.rest import BybitKline
from backend.market_data.bybit_ws_feed import BybitCandleFeed, CandleFeedSnapshot


class RecordingRestClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, str]] = []
        self.klines: list[BybitKline] = []

    def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int,
        category: str = "spot",
    ) -> list[BybitKline]:
        self.calls.append((symbol, interval, limit, category))
        return list(self.klines)


def _build_kline(index: int, *, close: float) -> BybitKline:
    opened_at = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=index)
    return BybitKline(
        start_time=opened_at,
        open_price=close - 1.0,
        high_price=close + 1.0,
        low_price=close - 2.0,
        close_price=close,
        volume=10.0 + index,
        turnover=1000.0 + index,
    )


@pytest.mark.asyncio
async def test_warm_up_populates_window_before_emitting_confirmed_candle() -> None:
    queue: asyncio.Queue[CandleFeedSnapshot] = asyncio.Queue()
    rest_client = RecordingRestClient()
    rest_client.klines = [_build_kline(0, close=100.0), _build_kline(1, close=101.0)]
    feed = BybitCandleFeed(
        symbols=["BTCUSDT"],
        interval="1",
        window_size=2,
        rest_client=rest_client,
        queue=queue,
    )
    feed._loop = asyncio.get_running_loop()  # noqa: SLF001

    await feed._warm_up_all()  # noqa: SLF001
    feed._on_kline_message(  # noqa: SLF001
        {
            "topic": "kline.1.BTCUSDT",
            "data": [
                {
                    "start": str(int(datetime(2024, 1, 1, 0, 2, tzinfo=UTC).timestamp() * 1000)),
                    "high": "103.0",
                    "low": "100.0",
                    "close": "102.0",
                    "volume": "15.0",
                    "confirm": True,
                }
            ],
        }
    )
    await asyncio.sleep(0)

    assert rest_client.calls == [("BTCUSDT", "1", 2, "spot")]
    snapshot = await asyncio.wait_for(queue.get(), timeout=1)
    assert snapshot.snapshot.symbol == "BTCUSDT"
    assert [candle.close for candle in snapshot.snapshot.candles] == [101.0, 102.0]
    assert snapshot.gap_recovered is False


@pytest.mark.asyncio
async def test_feed_ignores_unconfirmed_and_malformed_messages() -> None:
    queue: asyncio.Queue[CandleFeedSnapshot] = asyncio.Queue()
    rest_client = RecordingRestClient()
    rest_client.klines = [_build_kline(0, close=100.0)]
    feed = BybitCandleFeed(
        symbols=["BTCUSDT"],
        interval="1",
        window_size=1,
        rest_client=rest_client,
        queue=queue,
    )
    feed._loop = asyncio.get_running_loop()  # noqa: SLF001
    await feed._warm_up_all()  # noqa: SLF001

    feed._on_kline_message({"topic": "kline.1.BTCUSDT", "data": [{"confirm": False}]})  # noqa: SLF001
    feed._on_kline_message({"topic": "kline.1.BTCUSDT", "data": [{"confirm": True}]})  # noqa: SLF001
    await asyncio.sleep(0)

    assert queue.empty() is True
