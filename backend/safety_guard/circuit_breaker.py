from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.safety_guard.exceptions import (
    CooldownActiveError,
    DailyLossLimitError,
    HardLossStreakPauseError,
    WeeklyLossLimitError,
)
from backend.trade_journal.models import DailyStat, PauseReason, SystemEventType
from backend.trade_journal.store import TradeJournalStore


class CircuitBreaker:
    """
    Reads DailyStat before each order to enforce loss limits.

    Two tripwires:
    1. daily_loss_pct >= max_daily_loss_pct (3%) → DailyLossLimitError
    2. consecutive_losses >= max_consecutive_losses (3) → CooldownActiveError + cooldown
    3. consecutive_losses >= hard_pause_consecutive_losses (5) → manual hard pause

    DailyStat is updated from realized close events, and check() reads the
    current persisted state before each new submission.
    """

    async def _get_or_create_today(self, session: AsyncSession) -> DailyStat:
        store = TradeJournalStore(session)
        return await store.get_or_create_daily_stat(stat_date=datetime.now(UTC).date())

    async def check(self, session: AsyncSession) -> None:
        """
        Raises SafetyGuardError if trading must be halted.
        Call before every order submission, after kill switch guard.
        """
        stat = await self._get_or_create_today(session)
        store = TradeJournalStore(session)
        state = await store.clear_pause_if_expired()
        await session.flush()

        if state.pause_reason == PauseReason.DAILY_LOSS:
            raise DailyLossLimitError("Daily loss pause is active until manual reset.")

        if state.pause_reason == PauseReason.WEEKLY_LOSS:
            raise WeeklyLossLimitError("Weekly loss pause is active until manual reset.")

        if state.pause_reason == PauseReason.COOLDOWN:
            raise CooldownActiveError("Cooldown is active; new entries are temporarily blocked.")

        if state.pause_reason == PauseReason.HARD_LOSS_STREAK:
            raise HardLossStreakPauseError("Hard loss-streak pause is active until manual reset.")

        if stat.total_trades >= settings.max_trades_per_day:
            raise DailyLossLimitError(
                f"Daily trade cap of {settings.max_trades_per_day} reached.",
            )

        if stat.daily_loss_pct is not None and stat.daily_loss_pct >= Decimal(
            str(settings.max_daily_loss_pct),
        ):
            stat.circuit_breaker_triggered = True
            await store.set_pause(
                pause_reason=PauseReason.DAILY_LOSS,
                manual_reset_required=True,
            )
            await store.append_system_event(
                event_type=SystemEventType.CIRCUIT_BREAKER,
                description="Daily loss limit reached.",
                event_metadata={"daily_loss_pct": str(stat.daily_loss_pct)},
            )
            await session.commit()
            logger.warning("Circuit breaker: daily loss limit. pct={}", stat.daily_loss_pct)
            raise DailyLossLimitError(
                f"Daily loss {stat.daily_loss_pct:.2%} exceeds limit "
                f"{settings.max_daily_loss_pct:.2%}.",
            )

        weekly_loss = await self._calculate_weekly_loss_pct(session)
        if weekly_loss >= Decimal(str(settings.max_weekly_loss_pct)):
            await store.set_pause(
                pause_reason=PauseReason.WEEKLY_LOSS,
                manual_reset_required=True,
            )
            await store.append_system_event(
                event_type=SystemEventType.CIRCUIT_BREAKER,
                description="Weekly loss limit reached.",
                event_metadata={"weekly_loss_pct": str(weekly_loss)},
            )
            await session.commit()
            raise WeeklyLossLimitError(
                f"Weekly loss {weekly_loss:.2%} exceeds limit {settings.max_weekly_loss_pct:.2%}.",
            )

        if stat.consecutive_losses >= settings.hard_pause_consecutive_losses:
            await store.set_pause(
                pause_reason=PauseReason.HARD_LOSS_STREAK,
                manual_reset_required=True,
            )
            await store.append_system_event(
                event_type=SystemEventType.CIRCUIT_BREAKER,
                description=(
                    f"{stat.consecutive_losses} consecutive losses — hard loss-streak pause triggered."
                ),
                event_metadata={
                    "consecutive_losses": stat.consecutive_losses,
                    "hard_pause_consecutive_losses": settings.hard_pause_consecutive_losses,
                },
            )
            await session.commit()
            raise HardLossStreakPauseError("Hard loss-streak pause is active until manual reset.")

        if stat.consecutive_losses >= settings.max_consecutive_losses:
            cooldown_until = datetime.now(UTC) + timedelta(hours=settings.cooldown_hours)
            await store.set_pause(
                pause_reason=PauseReason.COOLDOWN,
                manual_reset_required=False,
                cooldown_until=cooldown_until,
            )
            await store.append_system_event(
                event_type=SystemEventType.CIRCUIT_BREAKER,
                description=f"{stat.consecutive_losses} consecutive losses — cooldown triggered.",
                event_metadata={
                    "consecutive_losses": stat.consecutive_losses,
                    "cooldown_hours": settings.cooldown_hours,
                    "cooldown_until": cooldown_until.isoformat(),
                },
            )
            await session.commit()
            logger.warning(
                "Circuit breaker: consecutive losses={}. Cooldown {}h.",
                stat.consecutive_losses,
                settings.cooldown_hours,
            )
            raise CooldownActiveError(
                f"{stat.consecutive_losses} consecutive losses. "
                f"Cooldown: {settings.cooldown_hours}h.",
            )

    async def record_loss(self, session: AsyncSession, loss_pct: Decimal) -> None:
        """Call when a position closes at a loss."""
        store = TradeJournalStore(session)
        stat = await store.record_daily_loss(stat_date=datetime.now(UTC).date(), loss_pct=loss_pct)
        await session.commit()
        logger.info(
            "Loss recorded. consecutive={} daily_loss_pct={}",
            stat.consecutive_losses,
            stat.daily_loss_pct,
        )

    async def record_win(self, session: AsyncSession) -> None:
        """Call when a position closes at a profit. Resets consecutive loss counter."""
        store = TradeJournalStore(session)
        stat = await store.record_daily_win(stat_date=datetime.now(UTC).date())
        await session.commit()
        logger.info(
            "Win recorded. winning_trades={} consecutive losses reset.",
            stat.winning_trades,
        )

    async def increment_trade_count(self, session: AsyncSession) -> None:
        store = TradeJournalStore(session)
        await store.increment_daily_trade_count(stat_date=datetime.now(UTC).date())

    async def _calculate_weekly_loss_pct(self, session: AsyncSession) -> Decimal:
        week_start = datetime.now(UTC).date() - timedelta(days=6)
        result = await session.execute(
            select(func.sum(DailyStat.daily_loss_pct)).where(DailyStat.stat_date >= week_start),
        )
        value = result.scalar_one_or_none()
        if value is None:
            return Decimal(0)
        return Decimal(value)


circuit_breaker = CircuitBreaker()
