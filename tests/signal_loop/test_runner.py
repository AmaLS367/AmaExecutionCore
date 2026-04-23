from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.safety_guard.exceptions import CooldownActiveError
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.signal_loop.runner import SignalLoopRunner, _interval_to_minutes, _SymbolState
from backend.strategy_engine.contracts import StrategySignal
from backend.strategy_engine.service import StrategyExecutionRequest, StrategyExecutionResult
from backend.trade_journal.models import DailyStat


@dataclass(slots=True)
class _FakeSnapshot:
    symbol: str
    interval: str


class RecordingStrategyService:
    def __init__(self) -> None:
        self.calls: list[StrategyExecutionRequest] = []
        self.signals: dict[str, StrategySignal | None] = {}
        self.errors: dict[str, Exception] = {}

    async def run(
        self,
        request: StrategyExecutionRequest,
    ) -> StrategyExecutionResult[_FakeSnapshot]:
        self.calls.append(request)
        if request.symbol in self.errors:
            raise self.errors[request.symbol]
        snapshot = _FakeSnapshot(symbol=request.symbol, interval=request.interval)
        return StrategyExecutionResult(
            request=request,
            snapshot=snapshot,
            signal=self.signals.get(request.symbol),
        )


class RecordingExecutionService:
    def __init__(self) -> None:
        self.calls: list[ExecuteSignalRequest] = []
        self.error: Exception | None = None

    async def execute_signal(self, *, signal: ExecuteSignalRequest) -> object:
        self.calls.append(signal)
        if self.error is not None:
            raise self.error
        return {"accepted": signal.symbol}


def _build_signal(symbol: str) -> StrategySignal:
    return StrategySignal(
        symbol=symbol,
        direction="long",
        entry=100.0,
        stop=95.0,
        target=110.0,
        reason=f"signal-{symbol}",
    )


@pytest.mark.asyncio
async def test_tick_calls_strategy_for_each_symbol() -> None:
    strategy_service = RecordingStrategyService()
    execution_service = RecordingExecutionService()
    strategy_service.signals = {
        "BTCUSDT": _build_signal("BTCUSDT"),
        "ETHUSDT": None,
    }
    runner = SignalLoopRunner(
        strategy_service=strategy_service,
        execution_service=execution_service,
        symbols=("BTCUSDT", "ETHUSDT"),
        interval="5",
        cooldown_seconds=120,
        max_symbols_concurrent=2,
    )

    await runner._tick()

    assert strategy_service.calls == [
        StrategyExecutionRequest(symbol="BTCUSDT", interval="5"),
        StrategyExecutionRequest(symbol="ETHUSDT", interval="5"),
    ]
    assert [call.symbol for call in execution_service.calls] == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_tick_skips_symbol_in_cooldown() -> None:
    strategy_service = RecordingStrategyService()
    execution_service = RecordingExecutionService()
    runner = SignalLoopRunner(
        strategy_service=strategy_service,
        execution_service=execution_service,
        symbols=("BTCUSDT",),
        interval="5",
        cooldown_seconds=300,
        max_symbols_concurrent=1,
    )
    state = runner._symbol_states["BTCUSDT"]
    state.record_entry()

    await runner._tick()

    assert strategy_service.calls == []
    assert execution_service.calls == []


@pytest.mark.asyncio
async def test_tick_isolates_per_symbol_errors() -> None:
    strategy_service = RecordingStrategyService()
    execution_service = RecordingExecutionService()
    strategy_service.errors["BTCUSDT"] = RuntimeError("boom")
    strategy_service.signals["ETHUSDT"] = _build_signal("ETHUSDT")
    runner = SignalLoopRunner(
        strategy_service=strategy_service,
        execution_service=execution_service,
        symbols=("BTCUSDT", "ETHUSDT"),
        interval="5",
        cooldown_seconds=120,
        max_symbols_concurrent=2,
    )

    await runner._tick()

    assert [request.symbol for request in strategy_service.calls] == ["BTCUSDT", "ETHUSDT"]
    assert [call.symbol for call in execution_service.calls] == ["ETHUSDT"]


@pytest.mark.asyncio
async def test_tick_stops_loop_on_safety_guard_error() -> None:
    strategy_service = RecordingStrategyService()
    strategy_service.signals["BTCUSDT"] = _build_signal("BTCUSDT")
    execution_service = RecordingExecutionService()
    execution_service.error = CooldownActiveError("cooldown")
    runner = SignalLoopRunner(
        strategy_service=strategy_service,
        execution_service=execution_service,
        symbols=("BTCUSDT",),
        interval="5",
        cooldown_seconds=120,
        max_symbols_concurrent=1,
    )

    await runner._tick()

    assert runner._stop_event.is_set() is True


@pytest.mark.asyncio
async def test_tick_skips_symbol_blacklisted_for_today(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    strategy_service = RecordingStrategyService()
    execution_service = RecordingExecutionService()
    strategy_service.signals["BTCUSDT"] = _build_signal("BTCUSDT")
    runner = SignalLoopRunner(
        strategy_service=strategy_service,
        execution_service=execution_service,
        symbols=("BTCUSDT",),
        interval="5",
        cooldown_seconds=120,
        max_symbols_concurrent=1,
        session_factory=sqlite_session_factory,
    )

    async with sqlite_session_factory() as session:
        session.add(
            DailyStat(
                stat_date=datetime.now(UTC).date(),
                symbol_stats={"BTCUSDT": {"wins": 0, "losses": 5, "consecutive_losses": 5}},
            ),
        )
        await session.commit()

    await runner._tick()

    assert strategy_service.calls == []
    assert execution_service.calls == []


def test_symbol_state_cooldown_uses_configured_seconds() -> None:
    state = _SymbolState(symbol="BTCUSDT", cooldown_seconds=120)
    assert state.is_in_cooldown(now=datetime(2024, 1, 1, tzinfo=UTC)) is False

    state.last_entry_at = datetime(2024, 1, 1, tzinfo=UTC)
    assert state.is_in_cooldown(now=datetime(2024, 1, 1, 0, 1, 30, tzinfo=UTC)) is True
    assert state.is_in_cooldown(now=datetime(2024, 1, 1, 0, 2, 1, tzinfo=UTC)) is False


@pytest.mark.asyncio
async def test_run_forever_calls_tick_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = SignalLoopRunner(
        strategy_service=RecordingStrategyService(),
        execution_service=RecordingExecutionService(),
        symbols=("BTCUSDT",),
        interval="5",
        cooldown_seconds=120,
        max_symbols_concurrent=1,
    )
    calls: list[str] = []

    async def _tick() -> None:
        calls.append("tick")
        runner.stop()

    async def _sleep() -> None:
        calls.append("sleep")

    monkeypatch.setattr(runner, "_tick", _tick)
    monkeypatch.setattr(runner, "_sleep_until_next_candle_close", _sleep)

    await runner.run_forever()

    assert calls == ["tick", "sleep"]


@pytest.mark.asyncio
async def test_evaluate_symbol_raises_on_invalid_signal_direction() -> None:
    strategy_service = RecordingStrategyService()
    strategy_service.signals["BTCUSDT"] = StrategySignal(
        symbol="BTCUSDT",
        direction="sideways",
        entry=100.0,
        stop=95.0,
        target=110.0,
    )
    runner = SignalLoopRunner(
        strategy_service=strategy_service,
        execution_service=RecordingExecutionService(),
        symbols=("BTCUSDT",),
        interval="5",
        cooldown_seconds=120,
        max_symbols_concurrent=1,
    )

    with pytest.raises(ValueError, match="Unsupported strategy signal direction"):
        await runner._evaluate_symbol(
            runner._symbol_states["BTCUSDT"],
            asyncio.Semaphore(1),
        )


@pytest.mark.asyncio
async def test_evaluate_symbol_re_raises_safety_guard_from_strategy() -> None:
    strategy_service = RecordingStrategyService()
    strategy_service.errors["BTCUSDT"] = CooldownActiveError("paused")
    runner = SignalLoopRunner(
        strategy_service=strategy_service,
        execution_service=RecordingExecutionService(),
        symbols=("BTCUSDT",),
        interval="5",
        cooldown_seconds=120,
        max_symbols_concurrent=1,
    )

    with pytest.raises(CooldownActiveError, match="paused"):
        await runner._evaluate_symbol(
            runner._symbol_states["BTCUSDT"],
            asyncio.Semaphore(1),
        )
    assert runner._stop_event.is_set() is True


@pytest.mark.asyncio
async def test_sleep_until_next_candle_close_returns_when_stop_event_is_set() -> None:
    runner = SignalLoopRunner(
        strategy_service=RecordingStrategyService(),
        execution_service=RecordingExecutionService(),
        symbols=("BTCUSDT",),
        interval="5",
        cooldown_seconds=120,
        max_symbols_concurrent=1,
    )
    runner.stop()

    await runner._sleep_until_next_candle_close()


def test_interval_to_minutes_rejects_unknown_interval() -> None:
    with pytest.raises(ValueError, match="Unknown interval"):
        _interval_to_minutes("bad")
