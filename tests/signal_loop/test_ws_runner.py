from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.market_data.bybit_ws_feed import CandleFeedSnapshot
from backend.market_data.contracts import MarketCandle, MarketSnapshot
from backend.risk_manager.exceptions import InsufficientSpotBalanceError
from backend.safety_guard.exceptions import SafetyGuardError
from backend.signal_loop.ws_runner import WebSocketSignalRunner
from backend.strategy_engine.contracts import StrategySignal
from backend.trade_journal.models import DailyStat


def _snapshot(symbol: str = "BTCUSDT") -> MarketSnapshot:
    now = datetime.now(UTC)
    candles = tuple(
        MarketCandle(
            opened_at=now - timedelta(minutes=idx),
            high=101 + idx,
            low=99 + idx,
            close=100 + idx,
            volume=1000 + idx,
        )
        for idx in range(3)
    )
    return MarketSnapshot(symbol=symbol, interval="5", candles=candles)


class _Feed:
    def __init__(self, items: list[CandleFeedSnapshot] | None = None) -> None:
        self.queue: asyncio.Queue[CandleFeedSnapshot] = asyncio.Queue()
        self.started = False
        self.stopped = False
        self._items = items or []

    async def start(self) -> None:
        self.started = True
        for item in self._items:
            await self.queue.put(item)

    def stop(self) -> None:
        self.stopped = True


class _Strategy:
    def __init__(
        self,
        *,
        signal: StrategySignal | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.signal = signal
        self.exc = exc
        self.calls = 0

    async def generate_signal(self, snapshot: MarketSnapshot) -> StrategySignal | None:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.signal


class _ExecutionService:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[object] = []

    async def execute_signal(self, *, signal: object) -> object:
        self.calls.append(signal)
        if self.exc is not None:
            raise self.exc
        return {"ok": True}


def _capture_logs() -> tuple[list[str], int]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{level}|{message}")
    return messages, sink_id


@pytest.mark.asyncio
async def test_run_forever_starts_feed_processes_snapshot_and_stops() -> None:
    feed = _Feed([CandleFeedSnapshot(snapshot=_snapshot())])
    runner = WebSocketSignalRunner(
        strategy=_Strategy(),
        execution_service=_ExecutionService(),
        feed=feed,
        cooldown_seconds=60,
    )
    processed: list[str] = []

    async def _process(feed_snapshot: CandleFeedSnapshot) -> None:
        processed.append(feed_snapshot.snapshot.symbol)
        runner.stop()

    runner._process_feed_snapshot = _process  # type: ignore[method-assign]

    await runner.run_forever()

    assert feed.started is True
    assert feed.stopped is True
    assert processed == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_process_feed_snapshot_executes_signal_and_records_entry() -> None:
    strategy = _Strategy(
        signal=StrategySignal(
            symbol="BTCUSDT",
            direction="long",
            entry=100.0,
            stop=90.0,
            target=130.0,
        ),
    )
    execution_service = _ExecutionService()
    runner = WebSocketSignalRunner(
        strategy=strategy,
        execution_service=execution_service,
        feed=_Feed(),
        cooldown_seconds=60,
    )

    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot()))

    assert len(execution_service.calls) == 1
    assert runner._symbol_states["BTCUSDT"].last_entry_at is not None


@pytest.mark.asyncio
async def test_process_feed_snapshot_ignores_gap_recovered_and_cooldown() -> None:
    strategy = _Strategy(
        signal=StrategySignal(
            symbol="BTCUSDT",
            direction="long",
            entry=100.0,
            stop=90.0,
            target=130.0,
        ),
    )
    execution_service = _ExecutionService()
    runner = WebSocketSignalRunner(
        strategy=strategy,
        execution_service=execution_service,
        feed=_Feed(),
        cooldown_seconds=3600,
    )

    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot(), gap_recovered=True))
    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot()))
    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot()))

    assert len(execution_service.calls) == 1


@pytest.mark.asyncio
async def test_process_feed_snapshot_logs_cooldown_skip() -> None:
    messages, sink_id = _capture_logs()
    strategy = _Strategy(
        signal=StrategySignal(
            symbol="BTCUSDT",
            direction="long",
            entry=100.0,
            stop=90.0,
            target=130.0,
        ),
    )
    runner = WebSocketSignalRunner(
        strategy=strategy,
        execution_service=_ExecutionService(),
        feed=_Feed(),
        cooldown_seconds=3600,
    )
    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot()))
    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot()))
    logger.remove(sink_id)

    assert any("INFO|WebSocket snapshot skipped. symbol=BTCUSDT reason=cooldown" in message for message in messages)


@pytest.mark.asyncio
async def test_process_feed_snapshot_logs_blacklist_skip(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    messages, sink_id = _capture_logs()
    runner = WebSocketSignalRunner(
        strategy=_Strategy(),
        execution_service=_ExecutionService(),
        feed=_Feed(),
        cooldown_seconds=60,
        session_factory=sqlite_session_factory,
    )

    async with sqlite_session_factory() as session:
        session.add(
            DailyStat(
                stat_date=date.today(),
                symbol_stats={"BTCUSDT": {"consecutive_losses": 5}},
            ),
        )
        await session.commit()

    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot()))
    logger.remove(sink_id)

    assert any("INFO|WebSocket snapshot skipped. symbol=BTCUSDT reason=blacklist" in message for message in messages)


@pytest.mark.asyncio
async def test_process_feed_snapshot_logs_stale_skip() -> None:
    messages, sink_id = _capture_logs()
    settings.market_data_max_staleness_intervals = 1
    settings.market_data_staleness_grace_seconds = 0
    stale_snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        interval="5",
        candles=(
            MarketCandle(
                opened_at=datetime(2024, 1, 1, tzinfo=UTC),
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000.0,
            ),
        ),
    )
    runner = WebSocketSignalRunner(
        strategy=_Strategy(),
        execution_service=_ExecutionService(),
        feed=_Feed(),
        cooldown_seconds=60,
    )

    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=stale_snapshot))
    logger.remove(sink_id)

    assert any("INFO|WebSocket snapshot skipped. symbol=BTCUSDT reason=stale_snapshot" in message for message in messages)


@pytest.mark.asyncio
async def test_process_feed_snapshot_logs_balance_reject_without_stopping_runner() -> None:
    messages, sink_id = _capture_logs()
    runner = WebSocketSignalRunner(
        strategy=_Strategy(
            signal=StrategySignal(
                symbol="BTCUSDT",
                direction="long",
                entry=100.0,
                stop=90.0,
                target=130.0,
            ),
        ),
        execution_service=_ExecutionService(
            exc=InsufficientSpotBalanceError("Insufficient spot quote balance for BTCUSDT"),
        ),
        feed=_Feed(),
        cooldown_seconds=60,
    )

    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot()))
    logger.remove(sink_id)

    assert runner._stop_event.is_set() is False
    assert any("INFO|WebSocket signal rejected. symbol=BTCUSDT reason=insufficient_balance" in message for message in messages)


@pytest.mark.asyncio
async def test_process_feed_snapshot_handles_strategy_errors() -> None:
    runner = WebSocketSignalRunner(
        strategy=_Strategy(exc=RuntimeError("boom")),
        execution_service=_ExecutionService(),
        feed=_Feed(),
        cooldown_seconds=60,
    )

    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot()))

    assert runner._symbol_states["BTCUSDT"].last_entry_at is None


@pytest.mark.asyncio
async def test_process_feed_snapshot_stops_on_safety_guard_error() -> None:
    runner = WebSocketSignalRunner(
        strategy=_Strategy(
            signal=StrategySignal(
                symbol="BTCUSDT",
                direction="long",
                entry=100.0,
                stop=90.0,
                target=130.0,
            ),
        ),
        execution_service=_ExecutionService(exc=SafetyGuardError("blocked")),
        feed=_Feed(),
        cooldown_seconds=60,
    )

    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot()))

    assert runner._stop_event.is_set()


@pytest.mark.asyncio
async def test_process_feed_snapshot_ignores_invalid_direction() -> None:
    runner = WebSocketSignalRunner(
        strategy=_Strategy(
            signal=StrategySignal(
                symbol="BTCUSDT",
                direction="sideways",
                entry=100.0,
                stop=90.0,
                target=130.0,
            ),
        ),
        execution_service=_ExecutionService(),
        feed=_Feed(),
        cooldown_seconds=60,
    )

    await runner._process_feed_snapshot(CandleFeedSnapshot(snapshot=_snapshot()))

    assert runner._symbol_states["BTCUSDT"].last_entry_at is None


@pytest.mark.asyncio
async def test_is_symbol_blacklisted_uses_daily_stat_symbol_losses(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with sqlite_session_factory() as session:
        session.add(
            DailyStat(
                stat_date=date.today(),
                symbol_stats={"BTCUSDT": {"consecutive_losses": 5}},
            ),
        )
        await session.commit()

    runner = WebSocketSignalRunner(
        strategy=_Strategy(),
        execution_service=_ExecutionService(),
        feed=_Feed(),
        cooldown_seconds=60,
        session_factory=sqlite_session_factory,
    )

    assert await runner._is_symbol_blacklisted("BTCUSDT") is True
    assert await runner._is_symbol_blacklisted("ETHUSDT") is False


@pytest.mark.asyncio
async def test_is_symbol_blacklisted_returns_false_without_session_factory() -> None:
    runner = WebSocketSignalRunner(
        strategy=_Strategy(),
        execution_service=_ExecutionService(),
        feed=_Feed(),
        cooldown_seconds=60,
    )

    assert await runner._is_symbol_blacklisted("BTCUSDT") is False
