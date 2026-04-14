from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from loguru import logger

from backend.safety_guard.exceptions import SafetyGuardError
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.strategy_engine.service import StrategyExecutionRequest
from backend.trade_journal.store import TradeJournalStore


class SupportsStrategyService(Protocol):
    async def run(self, request: StrategyExecutionRequest) -> Any:
        ...


class SupportsExecutionService(Protocol):
    async def execute_signal(self, *, signal: ExecuteSignalRequest) -> object:
        ...


@dataclass(slots=True)
class _SymbolState:
    symbol: str
    cooldown_seconds: int
    last_entry_at: datetime | None = None

    def is_in_cooldown(self, *, now: datetime | None = None) -> bool:
        if self.last_entry_at is None:
            return False
        current_time = now or datetime.now(UTC)
        return (current_time - self.last_entry_at).total_seconds() < self.cooldown_seconds

    def record_entry(self, *, at: datetime | None = None) -> None:
        self.last_entry_at = at or datetime.now(UTC)


class SignalLoopRunner:
    def __init__(
        self,
        *,
        strategy_service: SupportsStrategyService,
        execution_service: SupportsExecutionService,
        symbols: tuple[str, ...] | list[str],
        interval: str,
        cooldown_seconds: int,
        max_symbols_concurrent: int,
        session_factory: Any | None = None,
    ) -> None:
        if not symbols:
            raise ValueError("SignalLoopRunner requires at least one symbol.")
        self._strategy_service = strategy_service
        self._execution_service = execution_service
        self._interval = interval
        self._max_symbols_concurrent = max_symbols_concurrent
        self._session_factory = session_factory
        self._symbol_states = {
            symbol: _SymbolState(symbol=symbol, cooldown_seconds=cooldown_seconds)
            for symbol in symbols
        }
        self._stop_event = asyncio.Event()

    async def run_forever(self) -> None:
        logger.info(
            "SignalLoopRunner started. symbols={} interval={}",
            list(self._symbol_states.keys()),
            self._interval,
        )
        while not self._stop_event.is_set():
            await self._tick()
            await self._sleep_until_next_candle_close()

    def stop(self) -> None:
        self._stop_event.set()

    async def _tick(self) -> None:
        semaphore = asyncio.Semaphore(self._max_symbols_concurrent)
        tasks = [self._evaluate_symbol(state, semaphore) for state in self._symbol_states.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _evaluate_symbol(self, state: _SymbolState, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            if state.is_in_cooldown() or await self._is_symbol_blacklisted(state.symbol):
                return

            try:
                result = await self._strategy_service.run(
                    StrategyExecutionRequest(symbol=state.symbol, interval=self._interval)
                )
            except SafetyGuardError:
                self.stop()
                raise
            except Exception:
                logger.exception("Signal loop strategy evaluation failed for {}.", state.symbol)
                return

            if result.signal is None:
                return

            signal = result.signal
            direction = signal.direction
            if direction not in ("long", "short"):
                raise ValueError(f"Unsupported strategy signal direction: {direction}")
            try:
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
                logger.exception("Signal loop execution failed for {}.", signal.symbol)

    async def _sleep_until_next_candle_close(self) -> None:
        interval_minutes = _interval_to_minutes(self._interval)
        now = datetime.now(UTC)
        total_minutes = now.hour * 60 + now.minute
        elapsed_in_period = total_minutes % interval_minutes
        minutes_to_next = interval_minutes - elapsed_in_period
        wait_seconds = minutes_to_next * 60 - now.second + 2
        if wait_seconds <= 0:
            wait_seconds = interval_minutes * 60

        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            return

    async def _is_symbol_blacklisted(self, symbol: str) -> bool:
        if self._session_factory is None:
            return False
        async with self._session_factory() as session:
            store = TradeJournalStore(session)
            stat = await store.get_or_create_today_daily_stat()
            return store.symbol_consecutive_losses(stat, symbol) >= 5


def _interval_to_minutes(interval: str) -> int:
    mapping = {
        "1": 1,
        "3": 3,
        "5": 5,
        "15": 15,
        "30": 30,
        "60": 60,
        "120": 120,
        "240": 240,
        "D": 1440,
    }
    if interval not in mapping:
        raise ValueError(f"Unknown interval: {interval!r}")
    return mapping[interval]
