from __future__ import annotations

import asyncio
from typing import Any, Literal, Protocol, cast

from loguru import logger

from backend.config import settings
from backend.market_data.bybit_ws_feed import CandleFeedSnapshot
from backend.market_data.contracts import MarketSnapshot
from backend.market_data.staleness import (
    allowed_snapshot_staleness_seconds,
    is_snapshot_stale,
    snapshot_age_seconds,
)
from backend.risk_manager.exceptions import InsufficientSpotBalanceError
from backend.safety_guard.exceptions import SafetyGuardError
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.signal_loop.runner import _SymbolState
from backend.strategy_engine.contracts import StrategySignal
from backend.trade_journal.store import TradeJournalStore


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
            except TimeoutError:
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
        skip_reason = await self._skip_reason(feed_snapshot=feed_snapshot, state=state)
        if skip_reason is not None:
            logger.info(skip_reason, snapshot.symbol)
            return

        try:
            signal = await self._strategy.generate_signal(snapshot)
        except Exception:
            logger.exception("WebSocket strategy evaluation failed for {}.", snapshot.symbol)
            return

        if signal is None:
            return

        direction = signal.direction
        if direction not in ("long", "short"):
            logger.warning(
                "WebSocket signal rejected. symbol={} reason=unsupported_direction direction={}",
                signal.symbol,
                direction,
            )
            return

        try:
            await self._execution_service.execute_signal(
                signal=ExecuteSignalRequest(
                    symbol=signal.symbol,
                    direction=cast("Literal['long', 'short']", direction),
                    entry=signal.entry,
                    stop=signal.stop,
                    target=signal.target,
                    reason=signal.reason,
                    strategy_version=signal.strategy_version,
                    indicators_snapshot=signal.indicators_snapshot,
                ),
            )
            state.record_entry()
        except InsufficientSpotBalanceError as exc:
            logger.info(
                "WebSocket signal rejected. symbol={} reason=insufficient_balance detail={}",
                signal.symbol,
                exc,
            )
        except SafetyGuardError:
            self.stop()
        except Exception:
            logger.exception("WebSocket execution failed for {}.", signal.symbol)

    async def _skip_reason(
        self,
        *,
        feed_snapshot: CandleFeedSnapshot,
        state: _SymbolState,
    ) -> str | None:
        snapshot = feed_snapshot.snapshot
        if feed_snapshot.gap_recovered:
            return "WebSocket snapshot skipped. symbol={} reason=gap_recovered"
        if is_snapshot_stale(
            snapshot,
            max_staleness_intervals=settings.market_data_max_staleness_intervals,
            grace_seconds=settings.market_data_staleness_grace_seconds,
        ):
            return (
                "WebSocket snapshot skipped. symbol={} reason=stale_snapshot "
                f"age_seconds={snapshot_age_seconds(snapshot):.1f} "
                f"allowed_seconds={allowed_snapshot_staleness_seconds(snapshot, max_staleness_intervals=settings.market_data_max_staleness_intervals, grace_seconds=settings.market_data_staleness_grace_seconds)}"
            )
        if state.is_in_cooldown():
            return "WebSocket snapshot skipped. symbol={} reason=cooldown"
        if await self._is_symbol_blacklisted(snapshot.symbol):
            return "WebSocket snapshot skipped. symbol={} reason=blacklist"
        return None

    async def _is_symbol_blacklisted(self, symbol: str) -> bool:
        if self._session_factory is None:
            return False
        async with self._session_factory() as session:
            store = TradeJournalStore(session)
            stat = await store.get_or_create_today_daily_stat()
            return store.symbol_consecutive_losses(stat, symbol) >= 5
