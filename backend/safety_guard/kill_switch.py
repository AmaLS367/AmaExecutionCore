import asyncio

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.bybit_client.rest import BybitRESTClient
from backend.config import settings
from backend.safety_guard.exceptions import KillSwitchActiveError
from backend.trade_journal.models import SystemEvent, SystemEventType, Trade, TradeStatus

_CANCELLABLE_STATUSES = {
    TradeStatus.ORDER_SUBMITTED,
    TradeStatus.ORDER_PARTIALLY_FILLED,
}


class KillSwitch:
    """
    Global halt mechanism. Sets an in-memory flag for zero-latency checks
    on the hot path, and records a SystemEvent in the DB for audit.

    Per project rules: does NOT auto-close open positions.
    Closing open positions requires explicit manual action.
    """

    def __init__(self) -> None:
        self._active: bool = False

    def is_active(self) -> bool:
        return self._active

    def guard(self) -> None:
        """Raises KillSwitchActiveError if active. Call before every order submission."""
        if self._active:
            raise KillSwitchActiveError("Kill switch is active — no new orders allowed.")

    async def activate(
        self,
        session: AsyncSession,
        rest_client: BybitRESTClient | None = None,
    ) -> None:
        """
        Activates the kill switch:
        1. Sets in-memory flag immediately.
        2. Cancels all pending exchange orders (if REST client provided and not shadow mode).
        3. Writes a SystemEvent to the DB.
        """
        if self._active:
            logger.warning("Kill switch already active — ignoring duplicate activation.")
            return

        self._active = True
        logger.warning("KILL SWITCH ACTIVATED.")

        if rest_client and settings.trading_mode != "shadow":
            await self._cancel_pending_orders(session, rest_client)

        session.add(
            SystemEvent(
                event_type=SystemEventType.KILL_SWITCH,
                description="Kill switch activated.",
                event_metadata={"trading_mode": settings.trading_mode},
            )
        )
        await session.commit()

    async def _cancel_pending_orders(
        self, session: AsyncSession, rest_client: BybitRESTClient
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
