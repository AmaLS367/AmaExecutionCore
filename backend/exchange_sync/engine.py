import asyncio
from collections.abc import Coroutine
from contextlib import suppress
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.exchange_sync.listener import BybitWebSocketListener
from backend.trade_journal.models import ExitReason, Trade, TradeStatus, SystemEventType
from backend.trade_journal.store import TradeJournalStore

_ORDER_STATUS_MAP: dict[str, TradeStatus] = {
    "Filled": TradeStatus.ORDER_CONFIRMED,
    "Rejected": TradeStatus.ORDER_REJECTED,
    "Cancelled": TradeStatus.ORDER_CANCELLED,
    "PartiallyFilled": TradeStatus.ORDER_PARTIALLY_FILLED,
}
_ENTRY_RECONCILIATION_STATUSES: tuple[TradeStatus, ...] = (
    TradeStatus.ORDER_SUBMITTED,
    TradeStatus.ORDER_PENDING_UNKNOWN,
)
_CLOSE_RECONCILIATION_STATUSES: tuple[TradeStatus, ...] = (TradeStatus.POSITION_CLOSE_PENDING,)
_DEFAULT_RECONCILIATION_INTERVAL_SECONDS = 5.0


class OrderStatusClient(Protocol):
    def get_order_status(
        self,
        *,
        category: str,
        symbol: str,
        order_id: str | None = None,
        order_link_id: str | None = None,
    ) -> dict[str, object] | None: ...


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

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        rest_client: OrderStatusClient | None = None,
        reconciliation_interval_seconds: float = _DEFAULT_RECONCILIATION_INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._rest_client = rest_client
        self._reconciliation_interval_seconds = reconciliation_interval_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reconciliation_task: asyncio.Task[None] | None = None

    def wire(self, listener: BybitWebSocketListener) -> None:
        """Register handlers on the listener. Must be called after the event loop starts."""
        self._loop = asyncio.get_running_loop()
        listener.on_order(self._on_order)
        listener.on_execution(self._on_execution)
        logger.info("ExchangeSyncEngine wired to WebSocket listener.")

    def start_reconciliation_worker(self) -> None:
        if settings.trading_mode == "shadow" or self._rest_client is None:
            return
        if self._reconciliation_task is not None and not self._reconciliation_task.done():
            return
        self._reconciliation_task = asyncio.create_task(self._reconciliation_loop())
        logger.info("ExchangeSyncEngine reconciliation worker started.")

    async def stop_reconciliation_worker(self) -> None:
        if self._reconciliation_task is None:
            return
        self._reconciliation_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._reconciliation_task
        self._reconciliation_task = None

    async def _reconciliation_loop(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("Exchange reconciliation iteration failed.")
            await asyncio.sleep(self._reconciliation_interval_seconds)

    async def reconcile_once(self) -> int:
        if self._rest_client is None:
            return 0

        reconciled_entries = await self.reconcile_entry_orders()
        reconciled_closes = await self.reconcile_close_orders()
        return reconciled_entries + reconciled_closes

    async def reconcile_entry_orders(self) -> int:
        return await self._reconcile_status_group(_ENTRY_RECONCILIATION_STATUSES)

    async def reconcile_close_orders(self) -> int:
        return await self._reconcile_status_group(_CLOSE_RECONCILIATION_STATUSES)

    # ------------------------------------------------------------------
    # Thread → asyncio bridge
    # ------------------------------------------------------------------

    def _dispatch(self, coro: Coroutine[Any, Any, None]) -> None:
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
            store = TradeJournalStore(session)
            trade = await store.get_trade_by_order_link_id(order_link_id)
            if trade is None:
                logger.warning(
                    "WS order event: no trade found for order_link_id={}", order_link_id
                )
                return

            await self._apply_order_update(session=session, store=store, trade=trade, data=data)

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
            store = TradeJournalStore(session)
            trade = await store.get_trade_by_order_link_id(order_link_id)
            if trade is None:
                return

            if exec_fee:
                current_fee = trade.fee_paid or Decimal("0")
                trade.fee_paid = current_fee + Decimal(exec_fee)
            if trade.order_link_id == order_link_id and exec_price and trade.entry_price:
                trade.slippage = abs(Decimal(exec_price) - trade.entry_price)

            await session.commit()
            logger.debug("Execution recorded. order_link_id={}", order_link_id)

    async def _reconcile_trade(
        self,
        *,
        session: AsyncSession,
        store: TradeJournalStore,
        trade: Trade,
    ) -> bool:
        if self._rest_client is None:
            return False

        tracked_order_link_id = self._tracked_order_link_id(trade)
        if tracked_order_link_id is None:
            return False

        remote_order = await asyncio.to_thread(
            self._rest_client.get_order_status,
            category=trade.market_type.value,
            symbol=trade.symbol,
            order_link_id=tracked_order_link_id,
        )
        if remote_order is None:
            return False

        data = dict(remote_order)
        data.setdefault("orderLinkId", tracked_order_link_id)
        updated = await self._apply_order_update(
            session=session,
            store=store,
            trade=trade,
            data=data,
        )
        if updated:
            logger.info(
                "Reconciled trade {} from exchange state via order_link_id={}.",
                trade.id,
                tracked_order_link_id,
            )
        return updated

    async def _reconcile_status_group(self, statuses: tuple[TradeStatus, ...]) -> int:
        if self._rest_client is None:
            return 0

        reconciled = 0
        async with self._session_factory() as session:
            store = TradeJournalStore(session)
            trades = await store.list_trades_by_status(statuses)
            for trade in trades:
                try:
                    if await self._reconcile_trade(session=session, store=store, trade=trade):
                        reconciled += 1
                except Exception:
                    logger.exception("Failed to reconcile trade {}.", trade.id)
            await session.commit()
        return reconciled

    async def _apply_order_update(
        self,
        *,
        session: AsyncSession,
        store: TradeJournalStore,
        trade: Trade,
        data: dict[str, object],
    ) -> bool:
        order_link_id = str(data.get("orderLinkId", ""))
        order_status = str(data.get("orderStatus", ""))
        new_status = _ORDER_STATUS_MAP.get(order_status)
        if not order_link_id or new_status is None:
            return False

        is_close_order = trade.close_order_link_id == order_link_id
        exchange_order_id = data.get("orderId")
        if isinstance(exchange_order_id, str):
            if is_close_order:
                trade.close_exchange_order_id = exchange_order_id
            else:
                trade.exchange_order_id = exchange_order_id

        if (
            not is_close_order
            and trade.status == TradeStatus.POSITION_OPEN
            and new_status == TradeStatus.ORDER_CONFIRMED
            and data.get("leavesQty") == "0"
        ):
            return False

        if new_status == TradeStatus.ORDER_CONFIRMED and not is_close_order:
            avg_price = data.get("avgPrice")
            cum_exec_qty = data.get("cumExecQty")
            if avg_price is not None:
                trade.avg_fill_price = Decimal(str(avg_price))
            if cum_exec_qty is not None:
                trade.filled_qty = Decimal(str(cum_exec_qty))
            await store.transition_trade_status(
                trade,
                TradeStatus.ORDER_CONFIRMED,
                event_metadata={"source": "exchange_sync"},
            )
            trade.opened_at = datetime.now(timezone.utc)
            if data.get("leavesQty") == "0":
                await store.transition_trade_status(
                    trade,
                    TradeStatus.POSITION_OPEN,
                    event_metadata={"source": "exchange_sync"},
                )
        elif is_close_order and new_status == TradeStatus.ORDER_CONFIRMED:
            if trade.status == TradeStatus.PNL_RECORDED:
                return False
            await store.transition_trade_status(
                trade,
                TradeStatus.ORDER_CONFIRMED,
                event_metadata={"source": "exchange_sync"},
            )
            exit_price = Decimal(str(data.get("avgPrice", "0")))
            trade.avg_exit_price = exit_price
            await store.transition_trade_status(
                trade,
                TradeStatus.POSITION_CLOSED,
                event_metadata={"source": "exchange_sync"},
            )
            trade.closed_at = datetime.now(timezone.utc)
            if trade.opened_at is not None:
                opened_at = trade.opened_at
                closed_at = trade.closed_at
                if opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=timezone.utc)
                if closed_at.tzinfo is None:
                    closed_at = closed_at.replace(tzinfo=timezone.utc)
                trade.hold_time_seconds = int((closed_at - opened_at).total_seconds())
            if trade.exit_reason is None:
                trade.exit_reason = ExitReason.MANUAL

            realized_pnl = store.calculate_realized_pnl(trade, exit_price)
            trade.realized_pnl = realized_pnl
            trade.pnl_pct = store.calculate_pnl_pct(trade, realized_pnl)
            trade.pnl_in_r = store.calculate_pnl_in_r(trade, realized_pnl)
            await store.transition_trade_status(
                trade,
                TradeStatus.PNL_RECORDED,
                event_metadata={"source": "exchange_sync"},
            )
            await store.apply_trade_outcome_analytics(trade)
        elif is_close_order and new_status in {
            TradeStatus.ORDER_REJECTED,
            TradeStatus.ORDER_CANCELLED,
        }:
            await store.transition_trade_status(
                trade,
                new_status,
                event_metadata={"source": "exchange_sync"},
            )
            await store.transition_trade_status(
                trade,
                TradeStatus.POSITION_CLOSE_FAILED,
                event_metadata={"source": "exchange_sync"},
            )
            await store.append_system_event(
                event_type=SystemEventType.ERROR,
                description="Close order failed and position may remain open.",
                event_metadata={
                    "trade_id": str(trade.id),
                    "close_order_link_id": order_link_id,
                },
            )
        else:
            await store.transition_trade_status(
                trade,
                new_status,
                event_metadata={"source": "exchange_sync"},
            )

        return True

    @staticmethod
    def _tracked_order_link_id(trade: Trade) -> str | None:
        if trade.status in _CLOSE_RECONCILIATION_STATUSES:
            return trade.close_order_link_id
        if trade.status in _ENTRY_RECONCILIATION_STATUSES:
            return trade.order_link_id
        return None
