from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

from backend.bybit_client.rest import BybitKline
from backend.market_data.bybit_ws_feed import (
    BybitCandleFeed,
    CandleFeedSnapshot,
    _interval_to_seconds,
)
from backend.market_data.contracts import MarketCandle, MarketSnapshot


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


class RecordingWebSocket:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.streams: list[tuple[object, object, object]] = []
        self.exited = False
        self.raise_on_exit = False

    def kline_stream(self, *, interval: object, symbol: object, callback: object) -> None:
        self.streams.append((interval, symbol, callback))

    def exit(self) -> None:
        if self.raise_on_exit:
            raise RuntimeError("boom")
        self.exited = True


def _install_fake_pybit(
    monkeypatch: pytest.MonkeyPatch,
    instances: list[RecordingWebSocket],
) -> None:
    pybit_module = types.ModuleType("pybit")
    unified_trading_module = types.ModuleType("pybit.unified_trading")

    class WebSocket(RecordingWebSocket):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            instances.append(self)

    unified_trading_module.WebSocket = WebSocket
    pybit_module.unified_trading = unified_trading_module
    monkeypatch.setitem(sys.modules, "pybit", pybit_module)
    monkeypatch.setitem(sys.modules, "pybit.unified_trading", unified_trading_module)


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
    feed._loop = asyncio.get_running_loop()

    await feed._warm_up_all()
    feed._on_kline_message(
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
                },
            ],
        },
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
    feed._loop = asyncio.get_running_loop()
    await feed._warm_up_all()

    feed._on_kline_message({"topic": "kline.1.BTCUSDT", "data": [{"confirm": False}]})
    feed._on_kline_message({"topic": "kline.1.BTCUSDT", "data": [{"confirm": True}]})
    await asyncio.sleep(0)

    assert queue.empty() is True


@pytest.mark.asyncio
async def test_start_warms_up_and_subscribes_to_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[CandleFeedSnapshot] = asyncio.Queue()
    rest_client = RecordingRestClient()
    rest_client.klines = [_build_kline(0, close=100.0)]
    ws_instances: list[RecordingWebSocket] = []
    _install_fake_pybit(monkeypatch, ws_instances)
    feed = BybitCandleFeed(
        symbols=["BTCUSDT"],
        interval="1",
        window_size=1,
        rest_client=rest_client,
        queue=queue,
        testnet=True,
    )

    await feed.start()

    assert len(ws_instances) == 1
    assert ws_instances[0].kwargs == {"testnet": True, "channel_type": "spot"}
    assert ws_instances[0].streams[0][0] == 1
    assert ws_instances[0].streams[0][1] == "BTCUSDT"


def test_feed_requires_at_least_one_symbol() -> None:
    with pytest.raises(ValueError, match="requires at least one symbol"):
        BybitCandleFeed(symbols=[], interval="1", window_size=1, rest_client=RecordingRestClient())


def test_interval_to_seconds_rejects_unknown_interval() -> None:
    with pytest.raises(ValueError, match="Unknown interval"):
        _interval_to_seconds("bad")


@pytest.mark.asyncio
async def test_recover_gap_emits_gap_recovered_snapshot() -> None:
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
    feed._loop = asyncio.get_running_loop()

    await feed._recover_gap("BTCUSDT")
    await asyncio.sleep(0)

    snapshot = await asyncio.wait_for(queue.get(), timeout=1)
    assert snapshot.gap_recovered is True
    assert [candle.close for candle in snapshot.snapshot.candles] == [100.0, 101.0]


@pytest.mark.asyncio
async def test_handle_confirmed_candle_schedules_gap_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
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
    feed._loop = asyncio.get_running_loop()
    await feed._warm_up_all()
    last_candle = feed._windows["BTCUSDT"][-1]
    gap_candle_start = last_candle.opened_at + timedelta(minutes=3)
    scheduled: list[str] = []

    def _run_coroutine_threadsafe(coro: object, loop: asyncio.AbstractEventLoop) -> object:
        del loop
        if hasattr(coro, "close"):
            coro.close()
        scheduled.append("gap")
        return object()

    monkeypatch.setattr("backend.market_data.bybit_ws_feed.asyncio.run_coroutine_threadsafe", _run_coroutine_threadsafe)

    feed._handle_confirmed_candle(
        "BTCUSDT",
        {
            "start": str(int(gap_candle_start.timestamp() * 1000)),
            "high": "103.0",
            "low": "100.0",
            "close": "102.0",
            "volume": "15.0",
        },
    )

    assert scheduled == ["gap"]
    assert queue.empty() is True


def test_queue_snapshot_nowait_drops_when_queue_is_full() -> None:
    queue: asyncio.Queue[CandleFeedSnapshot] = asyncio.Queue(maxsize=1)
    feed = BybitCandleFeed(
        symbols=["BTCUSDT"],
        interval="1",
        window_size=1,
        rest_client=RecordingRestClient(),
        queue=queue,
    )
    market_snapshot = CandleFeedSnapshot(
        snapshot=MarketSnapshot(
            symbol="BTCUSDT",
            interval="1",
            candles=(
                MarketCandle(
                    opened_at=datetime(2024, 1, 1, tzinfo=UTC),
                    high=101.0,
                    low=99.0,
                    close=100.0,
                    volume=1.0,
                ),
            ),
        ),
    )
    queue.put_nowait(market_snapshot)

    feed._queue_snapshot_nowait(market_snapshot)

    assert queue.qsize() == 1


def test_stop_swallow_exceptions_from_websocket_exit() -> None:
    feed = BybitCandleFeed(
        symbols=["BTCUSDT"],
        interval="1",
        window_size=1,
        rest_client=RecordingRestClient(),
    )
    ws = RecordingWebSocket(testnet=True, channel_type="spot")
    ws.raise_on_exit = True
    feed._ws = ws

    feed.stop()

    assert feed._ws is None
