from datetime import date
from decimal import Decimal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.safety_guard.exceptions import CircuitBreakerTrippedError, DailyLossLimitError
from backend.trade_journal.models import DailyStat, SystemEvent, SystemEventType


class CircuitBreaker:
    """
    Reads DailyStat before each order to enforce loss limits.

    Two tripwires:
    1. daily_loss_pct >= max_daily_loss_pct (3%) → DailyLossLimitError
    2. consecutive_losses >= max_consecutive_losses (3) → CircuitBreakerTrippedError + cooldown

    record_loss / record_win update today's DailyStat and are called by
    ExchangeSyncEngine when positions close.
    """

    async def _get_or_create_today(self, session: AsyncSession) -> DailyStat:
        today = date.today()
        result = await session.execute(
            select(DailyStat).where(DailyStat.stat_date == today)
        )
        stat = result.scalar_one_or_none()
        if stat is None:
            stat = DailyStat(stat_date=today)
            session.add(stat)
            await session.flush()
        return stat

    async def check(self, session: AsyncSession) -> None:
        """
        Raises SafetyGuardError if trading must be halted.
        Call before every order submission, after kill switch guard.
        """
        stat = await self._get_or_create_today(session)

        if stat.daily_loss_pct is not None and stat.daily_loss_pct >= Decimal(
            str(settings.max_daily_loss_pct)
        ):
            stat.circuit_breaker_triggered = True
            session.add(
                SystemEvent(
                    event_type=SystemEventType.CIRCUIT_BREAKER,
                    description="Daily loss limit reached.",
                    event_metadata={"daily_loss_pct": str(stat.daily_loss_pct)},
                )
            )
            await session.commit()
            logger.warning("Circuit breaker: daily loss limit. pct={}", stat.daily_loss_pct)
            raise DailyLossLimitError(
                f"Daily loss {stat.daily_loss_pct:.2%} exceeds limit "
                f"{settings.max_daily_loss_pct:.2%}."
            )

        if stat.consecutive_losses >= settings.max_consecutive_losses:
            session.add(
                SystemEvent(
                    event_type=SystemEventType.CIRCUIT_BREAKER,
                    description=f"{stat.consecutive_losses} consecutive losses — cooldown triggered.",
                    event_metadata={
                        "consecutive_losses": stat.consecutive_losses,
                        "cooldown_hours": settings.cooldown_hours,
                    },
                )
            )
            await session.commit()
            logger.warning(
                "Circuit breaker: consecutive losses={}. Cooldown {}h.",
                stat.consecutive_losses,
                settings.cooldown_hours,
            )
            raise CircuitBreakerTrippedError(
                f"{stat.consecutive_losses} consecutive losses. "
                f"Cooldown: {settings.cooldown_hours}h."
            )

    async def record_loss(self, session: AsyncSession, loss_pct: Decimal) -> None:
        """Call when a position closes at a loss."""
        stat = await self._get_or_create_today(session)
        stat.losing_trades = (stat.losing_trades or 0) + 1
        stat.total_trades = (stat.total_trades or 0) + 1
        stat.consecutive_losses = (stat.consecutive_losses or 0) + 1
        stat.daily_loss_pct = (stat.daily_loss_pct or Decimal("0")) + loss_pct
        await session.commit()
        logger.info(
            "Loss recorded. consecutive={} daily_loss_pct={}",
            stat.consecutive_losses,
            stat.daily_loss_pct,
        )

    async def record_win(self, session: AsyncSession) -> None:
        """Call when a position closes at a profit. Resets consecutive loss counter."""
        stat = await self._get_or_create_today(session)
        stat.winning_trades = (stat.winning_trades or 0) + 1
        stat.total_trades = (stat.total_trades or 0) + 1
        stat.consecutive_losses = 0
        await session.commit()
        logger.info("Win recorded. consecutive losses reset.")


circuit_breaker = CircuitBreaker()
