from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from backend.market_data.contracts import MarketCandle, MarketSnapshot


@dataclass(slots=True, frozen=True)
class CandleFeedSnapshot:
    snapshot: MarketSnapshot
    gap_recovered: bool = False


class BybitCandleFeed:
    def __init__(
        self,
        *,
        symbols: list[str],
        interval: str,
        window_size: int,
        testnet: bool = False,
        rest_client: Any,
        queue: asyncio.Queue[CandleFeedSnapshot] | None = None,
    ) -> None:
        if not symbols:
            raise ValueError("BybitCandleFeed requires at least one symbol.")
        self._symbols = symbols
        self._interval = interval
        self._interval_seconds = _interval_to_seconds(interval)
        self._window_size = window_size
        self._testnet = testnet
        self._rest_client = rest_client
        self._queue: asyncio.Queue[CandleFeedSnapshot] = queue or asyncio.Queue(maxsize=500)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any | None = None
        self._lock = threading.Lock()
        self._windows: dict[str, deque[MarketCandle]] = {
            symbol: deque(maxlen=window_size) for symbol in symbols
        }
        self._warmed_up = {symbol: False for symbol in symbols}

    @property
    def queue(self) -> asyncio.Queue[CandleFeedSnapshot]:
        return self._queue

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._warm_up_all()
        self._start_ws()

    def stop(self) -> None:
        if self._ws is not None:
            try:
                self._ws.exit()
            except Exception:
                logger.exception("Failed to stop Bybit candle feed WebSocket.")
            self._ws = None

    async def _warm_up_all(self) -> None:
        for symbol in self._symbols:
            await self._warm_up_symbol(symbol)

    async def _warm_up_symbol(self, symbol: str) -> None:
        klines = await asyncio.to_thread(
            self._rest_client.get_klines,
            symbol=symbol,
            interval=self._interval,
            limit=self._window_size,
            category="spot",
        )
        ordered_klines = sorted(klines, key=lambda candle: candle.start_time)
        window = self._windows[symbol]
        window.clear()
        for kline in ordered_klines:
            window.append(
                MarketCandle(
                    opened_at=kline.start_time,
                    high=kline.high_price,
                    low=kline.low_price,
                    close=kline.close_price,
                    volume=kline.volume,
                )
            )
        self._warmed_up[symbol] = len(window) >= self._window_size

    def _start_ws(self) -> None:
        try:
            from pybit.unified_trading import WebSocket  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            logger.warning("pybit is not installed — public candle feed not started.")
            return

        self._ws = WebSocket(testnet=self._testnet, channel_type="spot")
        for symbol in self._symbols:
            self._ws.kline_stream(
                interval=int(self._interval) if self._interval.isdigit() else self._interval,
                symbol=symbol,
                callback=self._on_kline_message,
            )

    def _on_kline_message(self, message: dict[str, Any]) -> None:
        topic = str(message.get("topic", ""))
        parts = topic.split(".")
        if len(parts) < 3:
            return
        symbol = parts[2]

        for item in message.get("data", []):
            if not item.get("confirm", False):
                continue
            self._handle_confirmed_candle(symbol, item)

    def _handle_confirmed_candle(self, symbol: str, item: dict[str, Any]) -> None:
        try:
            candle = MarketCandle(
                opened_at=datetime.fromtimestamp(int(item["start"]) / 1000, tz=UTC),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=float(item.get("volume", 0.0)),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Malformed kline payload for {}: {}", symbol, item)
            return

        with self._lock:
            window = self._windows[symbol]
            if window and self._is_gap(window[-1], candle):
                if self._loop is not None:
                    asyncio.run_coroutine_threadsafe(self._recover_gap(symbol), self._loop)
                return

            window.append(candle)
            if not self._warmed_up[symbol]:
                self._warmed_up[symbol] = len(window) >= self._window_size
            if not self._warmed_up[symbol]:
                return

            snapshot = MarketSnapshot(symbol=symbol, interval=self._interval, candles=tuple(window))

        self._enqueue_snapshot(CandleFeedSnapshot(snapshot=snapshot))

    async def _recover_gap(self, symbol: str) -> None:
        await self._warm_up_symbol(symbol)
        if not self._warmed_up[symbol]:
            return
        snapshot = MarketSnapshot(
            symbol=symbol,
            interval=self._interval,
            candles=tuple(self._windows[symbol]),
        )
        self._enqueue_snapshot(CandleFeedSnapshot(snapshot=snapshot, gap_recovered=True))

    def _enqueue_snapshot(self, feed_snapshot: CandleFeedSnapshot) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._queue_snapshot_nowait, feed_snapshot)

    def _queue_snapshot_nowait(self, feed_snapshot: CandleFeedSnapshot) -> None:
        try:
            self._queue.put_nowait(feed_snapshot)
        except asyncio.QueueFull:
            logger.warning("Bybit candle feed queue is full. Dropping snapshot.")

    def _is_gap(self, last_candle: MarketCandle, new_candle: MarketCandle) -> bool:
        expected_next = last_candle.opened_at + timedelta(seconds=self._interval_seconds)
        return new_candle.opened_at > expected_next + timedelta(seconds=int(self._interval_seconds * 1.5))


def _interval_to_seconds(interval: str) -> int:
    mapping = {
        "1": 60,
        "3": 180,
        "5": 300,
        "15": 900,
        "30": 1800,
        "60": 3600,
        "120": 7200,
        "240": 14400,
        "D": 86400,
    }
    if interval not in mapping:
        raise ValueError(f"Unknown interval: {interval!r}")
    return mapping[interval]
