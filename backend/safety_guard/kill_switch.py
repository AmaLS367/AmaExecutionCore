import asyncio
from typing import Protocol

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.safety_guard.exceptions import KillSwitchActiveError
from backend.trade_journal.models import SystemEventType, Trade, TradeStatus
from backend.trade_journal.models import PauseReason
from backend.trade_journal.store import PersistedSafetyState, TradeJournalStore

_CANCELLABLE_STATUSES = {
    TradeStatus.ORDER_SUBMITTED,
    TradeStatus.ORDER_PENDING_UNKNOWN,
    TradeStatus.ORDER_PARTIALLY_FILLED,
}


class CancelOrderClient(Protocol):
    def cancel_order(
        self,
        *,
        category: str,
        symbol: str,
        order_id: str | None = None,
        order_link_id: str | None = None,
    ) -> dict[str, object]:
        ...


class KillSwitch:
    """
    Global halt mechanism. Sets an in-memory flag for zero-latency checks
    on the hot path, and records a SystemEvent in the DB for audit.

    Per project rules: does NOT auto-close open positions.
    Closing open positions requires explicit manual action.
    """

    def __init__(self) -> None:
        self._active: bool = False

    async def status(self, session: AsyncSession) -> PersistedSafetyState:
        store = TradeJournalStore(session)
        state = await store.clear_pause_if_expired()
        await session.commit()
        self._active = state.kill_switch_active
        return await store.read_safety_state()

    async def is_active(self, session: AsyncSession) -> bool:
        state = await self.status(session)
        return state.kill_switch_active

    async def guard(self, session: AsyncSession) -> None:
        """Raises KillSwitchActiveError if active. Call before every order submission."""
        if await self.is_active(session):
            raise KillSwitchActiveError("Kill switch is active — no new orders allowed.")

    async def activate(
        self,
        session: AsyncSession,
        rest_client: CancelOrderClient | None = None,
    ) -> None:
        """
        Activates the kill switch:
        1. Sets in-memory flag immediately.
        2. Cancels all pending exchange orders (if REST client provided and not shadow mode).
        3. Writes a SystemEvent to the DB.
        """
        if await self.is_active(session):
            logger.warning("Kill switch already active — ignoring duplicate activation.")
            return

        self._active = True
        logger.warning("KILL SWITCH ACTIVATED.")

        store = TradeJournalStore(session)
        await store.activate_kill_switch()

        if rest_client and settings.trading_mode != "shadow":
            await self._cancel_pending_orders(session, rest_client)

        await store.append_system_event(
            event_type=SystemEventType.KILL_SWITCH,
            description="Kill switch activated.",
            event_metadata={"trading_mode": settings.trading_mode},
        )
        await session.commit()

    async def reset(self, session: AsyncSession) -> PersistedSafetyState:
        store = TradeJournalStore(session)
        current_state = await store.get_or_create_safety_state()
        reset_consecutive_losses = current_state.pause_reason in {
            PauseReason.COOLDOWN,
            PauseReason.HARD_LOSS_STREAK,
        }
        await store.reset_safety_state(reset_consecutive_losses=reset_consecutive_losses)
        await store.append_system_event(
            event_type=SystemEventType.KILL_SWITCH,
            description="Safety state reset.",
            event_metadata={
                "trading_mode": settings.trading_mode,
                "reset_consecutive_losses": reset_consecutive_losses,
            },
        )
        await session.commit()
        self._active = False
        return await store.read_safety_state()

    async def _cancel_pending_orders(
        self, session: AsyncSession, rest_client: CancelOrderClient
    ) -> None:
        result = await session.execute(
            select(Trade).where(Trade.status.in_(list(_CANCELLABLE_STATUSES)))
        )
        pending = result.scalars().all()
        for trade in pending:
            try:
                await asyncio.to_thread(
                    rest_client.cancel_order,
                    category="spot",
                    symbol=trade.symbol,
                    order_link_id=trade.order_link_id,
                )
                logger.info("Cancelled pending order. order_link_id={}", trade.order_link_id)
            except Exception as exc:
                logger.error(
                    "Failed to cancel order {}. Manual intervention required. Error: {}",
                    trade.order_link_id,
                    exc,
                )


kill_switch = KillSwitch()
