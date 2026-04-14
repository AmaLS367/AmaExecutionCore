from __future__ import annotations

import asyncio
from typing import Any, Literal, Protocol, cast

from loguru import logger

from backend.market_data.bybit_ws_feed import CandleFeedSnapshot
from backend.market_data.contracts import MarketSnapshot
from backend.safety_guard.exceptions import SafetyGuardError
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.signal_loop.runner import _SymbolState
from backend.trade_journal.store import TradeJournalStore
from backend.strategy_engine.contracts import StrategySignal


class SupportsWebSocketStrategy(Protocol):
    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        ...


class SupportsExecutionService(Protocol):
    async def execute_signal(self, *, signal: ExecuteSignalRequest) -> object:
        ...


class SupportsCandleFeed(Protocol):
    @property
    def queue(self) -> asyncio.Queue[CandleFeedSnapshot]:
        ...

    async def start(self) -> None:
        ...

    def stop(self) -> None:
        ...


class WebSocketSignalRunner:
    def __init__(
        self,
        *,
        strategy: SupportsWebSocketStrategy,
        execution_service: SupportsExecutionService,
        feed: SupportsCandleFeed,
        cooldown_seconds: int,
        session_factory: Any | None = None,
    ) -> None:
        self._strategy = strategy
        self._execution_service = execution_service
        self._feed = feed
        self._cooldown_seconds = cooldown_seconds
        self._session_factory = session_factory
        self._symbol_states: dict[str, _SymbolState] = {}
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        await self._feed.start()
        logger.info("WebSocketSignalRunner started.")
        while not self._stop_event.is_set():
            try:
                feed_snapshot = await asyncio.wait_for(self._feed.queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            await self._process_feed_snapshot(feed_snapshot)

    def stop(self) -> None:
        self._stop_event.set()
        self._feed.stop()

    async def _process_feed_snapshot(self, feed_snapshot: CandleFeedSnapshot) -> None:
        snapshot = feed_snapshot.snapshot
        state = self._symbol_states.setdefault(
            snapshot.symbol,
            _SymbolState(symbol=snapshot.symbol, cooldown_seconds=self._cooldown_seconds),
        )
        if (
            state.is_in_cooldown()
            or feed_snapshot.gap_recovered
            or await self._is_symbol_blacklisted(snapshot.symbol)
        ):
            return

        try:
            signal = await self._strategy.generate_signal(snapshot)
        except Exception:
            logger.exception("WebSocket strategy evaluation failed for {}.", snapshot.symbol)
            return

        if signal is None:
            return

        try:
            direction = signal.direction
            if direction not in ("long", "short"):
                raise ValueError(f"Unsupported strategy signal direction: {direction}")
            await self._execution_service.execute_signal(
                signal=ExecuteSignalRequest(
                    symbol=signal.symbol,
                    direction=cast(Literal["long", "short"], direction),
                    entry=signal.entry,
                    stop=signal.stop,
                    target=signal.target,
                    reason=signal.reason,
                    strategy_version=signal.strategy_version,
                    indicators_snapshot=signal.indicators_snapshot,
                )
            )
            state.record_entry()
        except SafetyGuardError:
            self.stop()
        except Exception:
            logger.exception("WebSocket execution failed for {}.", signal.symbol)

    async def _is_symbol_blacklisted(self, symbol: str) -> bool:
        if self._session_factory is None:
            return False
        async with self._session_factory() as session:
            store = TradeJournalStore(session)
            stat = await store.get_or_create_today_daily_stat()
            return store.symbol_consecutive_losses(stat, symbol) >= 5
