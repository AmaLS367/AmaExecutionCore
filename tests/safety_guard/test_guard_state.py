from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.safety_guard.circuit_breaker import CircuitBreaker
from backend.safety_guard.exceptions import DailyLossLimitError
from backend.safety_guard.kill_switch import KillSwitch
from backend.trade_journal.models import DailyStat, SafetyState


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
            )
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
