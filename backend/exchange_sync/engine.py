import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.exchange_sync.listener import BybitWebSocketListener
from backend.trade_journal.models import Trade, TradeStatus

_ORDER_STATUS_MAP: dict[str, TradeStatus] = {
    "Filled": TradeStatus.ORDER_CONFIRMED,
    "Rejected": TradeStatus.ORDER_REJECTED,
    "Cancelled": TradeStatus.ORDER_CANCELLED,
    "PartiallyFilled": TradeStatus.ORDER_PARTIALLY_FILLED,
}


class ExchangeSyncEngine:
    """
    Bridges Bybit private WebSocket events to database state transitions.

    pybit runs its WebSocket in a background thread. This engine submits
    coroutines to the main asyncio event loop via run_coroutine_threadsafe,
    keeping all DB writes on the async event loop.

    State transitions handled:
      ORDER_SUBMITTED → ORDER_CONFIRMED → POSITION_OPEN
      ORDER_SUBMITTED → ORDER_REJECTED | ORDER_CANCELLED | ORDER_PARTIALLY_FILLED
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._loop: asyncio.AbstractEventLoop | None = None

    def wire(self, listener: BybitWebSocketListener) -> None:
        """Register handlers on the listener. Must be called after the event loop starts."""
        self._loop = asyncio.get_event_loop()
        listener.on_order(self._on_order)
        listener.on_execution(self._on_execution)
        logger.info("ExchangeSyncEngine wired to WebSocket listener.")

    # ------------------------------------------------------------------
    # Thread → asyncio bridge
    # ------------------------------------------------------------------

    def _dispatch(self, coro: Any) -> None:
        if self._loop is None or self._loop.is_closed():
            logger.warning("Event loop unavailable — WS event dropped.")
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ------------------------------------------------------------------
    # Raw WebSocket callbacks (called in pybit thread)
    # ------------------------------------------------------------------

    def _on_order(self, message: dict[str, Any]) -> None:
        for item in message.get("data", []):
            self._dispatch(self._process_order(item))

    def _on_execution(self, message: dict[str, Any]) -> None:
        for item in message.get("data", []):
            self._dispatch(self._process_execution(item))

    # ------------------------------------------------------------------
    # Async handlers (run on main event loop)
    # ------------------------------------------------------------------

    async def _process_order(self, data: dict[str, Any]) -> None:
        order_link_id: str = data.get("orderLinkId", "")
        order_status: str = data.get("orderStatus", "")
        if not order_link_id:
            return

        new_status = _ORDER_STATUS_MAP.get(order_status)
        if new_status is None:
            return

        async with self._session_factory() as session:
            result = await session.execute(
                select(Trade).where(Trade.order_link_id == order_link_id)
            )
            trade = result.scalar_one_or_none()
            if trade is None:
                logger.warning(
                    "WS order event: no trade found for order_link_id={}", order_link_id
                )
                return

            trade.status = new_status

            if new_status == TradeStatus.ORDER_CONFIRMED:
                avg_price = data.get("avgPrice")
                cum_exec_qty = data.get("cumExecQty")
                trade.avg_fill_price = Decimal(avg_price) if avg_price else None
                trade.filled_qty = Decimal(cum_exec_qty) if cum_exec_qty else None
                trade.opened_at = datetime.now(timezone.utc)
                # Fully filled → promote to POSITION_OPEN
                if data.get("leavesQty") == "0":
                    trade.status = TradeStatus.POSITION_OPEN

            await session.commit()
            logger.info(
                "Trade status updated. order_link_id={} status={}",
                order_link_id,
                trade.status.value,
            )

    async def _process_execution(self, data: dict[str, Any]) -> None:
        order_link_id: str = data.get("orderLinkId", "")
        if not order_link_id:
            return

        exec_fee = data.get("execFee")
        exec_price = data.get("execPrice")
        if not exec_fee and not exec_price:
            return

        async with self._session_factory() as session:
            result = await session.execute(
                select(Trade).where(Trade.order_link_id == order_link_id)
            )
            trade = result.scalar_one_or_none()
            if trade is None:
                return

            if exec_fee:
                trade.fee_paid = Decimal(exec_fee)
            if exec_price and trade.entry_price:
                trade.slippage = abs(Decimal(exec_price) - trade.entry_price)

            await session.commit()
            logger.debug("Execution recorded. order_link_id={}", order_link_id)
