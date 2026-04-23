from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.safety_guard.circuit_breaker import CircuitBreaker
from backend.safety_guard.exceptions import (
    CooldownActiveError,
    DailyLossLimitError,
    SafetyGuardError,
    WeeklyLossLimitError,
)
from backend.safety_guard.kill_switch import KillSwitch
from backend.trade_journal.models import DailyStat, PauseReason, SafetyState
from backend.trade_journal.store import TradeJournalStore


@pytest.mark.asyncio
async def test_circuit_breaker_persists_daily_pause_and_reset(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breaker = CircuitBreaker()
    kill_switch = KillSwitch()

    async with sqlite_session_factory() as session:
        session.add(
            DailyStat(
                stat_date=date.today(),
                daily_loss_pct=Decimal("0.04"),
            ),
        )
        await session.commit()

        with pytest.raises(DailyLossLimitError):
            await breaker.check(session)

        state = (await session.execute(select(SafetyState))).scalar_one()
        assert state.pause_reason == "daily_loss"
        assert state.manual_reset_required is True

        await kill_switch.reset(session)

        state = (await session.execute(select(SafetyState))).scalar_one()
        assert state.pause_reason is None
        assert state.manual_reset_required is False

        with pytest.raises(DailyLossLimitError):
            await breaker.check(session)


@pytest.mark.asyncio
async def test_circuit_breaker_weekly_loss_limit(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breaker = CircuitBreaker()
    kill_switch = KillSwitch()

    async with sqlite_session_factory() as session:
        session.add_all(
            [
                DailyStat(
                    stat_date=date.today() - timedelta(days=1),
                    daily_loss_pct=Decimal("0.03"),
                ),
                DailyStat(
                    stat_date=date.today(),
                    daily_loss_pct=Decimal("0.02"),
                ),
            ],
        )
        await session.commit()

        with pytest.raises(WeeklyLossLimitError):
            await breaker.check(session)

        await kill_switch.reset(session)

        with pytest.raises(WeeklyLossLimitError):
            await breaker.check(session)


@pytest.mark.asyncio
async def test_circuit_breaker_triggers_cooldown_after_three_losses(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breaker = CircuitBreaker()

    async with sqlite_session_factory() as session:
        session.add(
            DailyStat(
                stat_date=date.today(),
                consecutive_losses=3,
                daily_loss_pct=Decimal("0.01"),
            ),
        )
        await session.commit()

        with pytest.raises(CooldownActiveError):
            await breaker.check(session)

        state = (await session.execute(select(SafetyState))).scalar_one()
        assert state.pause_reason == PauseReason.COOLDOWN
        assert state.manual_reset_required is False
        assert state.cooldown_until is not None


@pytest.mark.asyncio
async def test_circuit_breaker_triggers_hard_pause_after_five_losses(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breaker = CircuitBreaker()

    async with sqlite_session_factory() as session:
        session.add(
            DailyStat(
                stat_date=date.today(),
                consecutive_losses=5,
                daily_loss_pct=Decimal("0.01"),
            ),
        )
        await session.commit()

        with pytest.raises(SafetyGuardError, match="manual reset"):
            await breaker.check(session)

        state = (await session.execute(select(SafetyState))).scalar_one()
        assert state.pause_reason is not None
        assert state.pause_reason.value == "hard_loss_streak"
        assert state.manual_reset_required is True
        assert state.cooldown_until is None

        with pytest.raises(SafetyGuardError, match="manual reset"):
            await breaker.check(session)


@pytest.mark.asyncio
async def test_cooldown_auto_expires_after_deadline(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breaker = CircuitBreaker()

    async with sqlite_session_factory() as session:
        session.add(
            DailyStat(
                stat_date=date.today(),
                consecutive_losses=3,
                daily_loss_pct=Decimal("0.01"),
            ),
        )
        store = TradeJournalStore(session)
        await store.set_pause(
            pause_reason=PauseReason.COOLDOWN,
            manual_reset_required=False,
            cooldown_until=datetime.now(UTC) - timedelta(seconds=1),
        )
        await session.commit()

        await breaker.check(session)

        state = (await session.execute(select(SafetyState))).scalar_one()
        stat = (await session.execute(select(DailyStat))).scalar_one()
        assert state.pause_reason is None
        assert state.cooldown_until is None
        assert stat.consecutive_losses == 0


@pytest.mark.asyncio
async def test_manual_reset_clears_hard_pause_and_loss_streak(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breaker = CircuitBreaker()
    kill_switch = KillSwitch()

    async with sqlite_session_factory() as session:
        session.add(
            DailyStat(
                stat_date=date.today(),
                consecutive_losses=5,
                daily_loss_pct=Decimal("0.01"),
            ),
        )
        await session.commit()

        with pytest.raises(SafetyGuardError):
            await breaker.check(session)

        await kill_switch.reset(session)

        state = (await session.execute(select(SafetyState))).scalar_one()
        stat = (await session.execute(select(DailyStat))).scalar_one()
        assert state.pause_reason is None
        assert state.manual_reset_required is False
        assert stat.consecutive_losses == 0


@pytest.mark.asyncio
async def test_circuit_breaker_get_or_create_today_is_safe_under_concurrent_first_access(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breaker = CircuitBreaker()

    async def worker() -> int | Exception:
        try:
            async with sqlite_session_factory() as session:
                stat = await breaker._get_or_create_today(session)
                await asyncio.sleep(0.05)
                await session.commit()
                return stat.id
        except Exception as exc:  # pragma: no cover - failure is asserted below
            return exc

    results = await asyncio.gather(worker(), worker())

    async with sqlite_session_factory() as session:
        stats = (await session.execute(select(DailyStat))).scalars().all()

    assert all(not isinstance(result, Exception) for result in results)
    assert len(stats) == 1


@pytest.mark.asyncio
async def test_increment_trade_count_counts_both_concurrent_submissions(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breaker = CircuitBreaker()

    async with sqlite_session_factory() as session:
        session.add(DailyStat(stat_date=date.today(), total_trades=0))
        await session.commit()

    async def worker() -> None:
        async with sqlite_session_factory() as session:
            await breaker.increment_trade_count(session)
            await session.commit()

    first = asyncio.create_task(worker())
    second = asyncio.create_task(worker())
    await asyncio.gather(first, second)

    async with sqlite_session_factory() as session:
        persisted_stat = (await session.execute(select(DailyStat))).scalar_one()

    assert persisted_stat.total_trades == 2


@pytest.mark.asyncio
async def test_record_loss_accumulates_both_concurrent_losses(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breaker = CircuitBreaker()

    async with sqlite_session_factory() as session:
        session.add(
            DailyStat(
                stat_date=date.today(),
                losing_trades=0,
                consecutive_losses=0,
                daily_loss_pct=Decimal(0),
            ),
        )
        await session.commit()

    async def worker(loss_pct: Decimal) -> None:
        async with sqlite_session_factory() as session:
            await breaker.record_loss(session, loss_pct)

    first = asyncio.create_task(worker(Decimal("0.01")))
    second = asyncio.create_task(worker(Decimal("0.02")))
    await asyncio.gather(first, second)

    async with sqlite_session_factory() as session:
        persisted_stat = (await session.execute(select(DailyStat))).scalar_one()

    assert persisted_stat.losing_trades == 2
    assert persisted_stat.consecutive_losses == 2
    assert persisted_stat.daily_loss_pct == Decimal("0.03")


@pytest.mark.asyncio
async def test_record_win_counts_both_concurrent_wins(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breaker = CircuitBreaker()

    async with sqlite_session_factory() as session:
        session.add(
            DailyStat(
                stat_date=date.today(),
                winning_trades=0,
                consecutive_losses=2,
            ),
        )
        await session.commit()

    async def worker() -> None:
        async with sqlite_session_factory() as session:
            await breaker.record_win(session)

    first = asyncio.create_task(worker())
    second = asyncio.create_task(worker())
    await asyncio.gather(first, second)

    async with sqlite_session_factory() as session:
        persisted_stat = (await session.execute(select(DailyStat))).scalar_one()

    assert persisted_stat.winning_trades == 2
    assert persisted_stat.consecutive_losses == 0
