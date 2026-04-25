from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.grid_engine.models import GridSlotRecord, GridSlotRecordStatus
from backend.grid_engine.order_manager import GridOrderManager


@dataclass(frozen=True, slots=True)
class GridOrderFillEvent:
    order_id: str
    side: str
    symbol: str


class GridWebSocketHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        order_manager: GridOrderManager,
    ) -> None:
        self._session_factory = session_factory
        self._order_manager = order_manager

    async def handle_order_fill(self, event: GridOrderFillEvent) -> None:
        async with self._session_factory() as session:
            slot = await self._slot_for_order_id(session, event.order_id)
            if slot is None:
                return
            if event.side == "Buy":
                order_id = self._order_manager.place_sell_limit(
                    event.symbol,
                    price=float(slot.sell_price),
                    qty=float(slot.units or Decimal(0)),
                )
                slot.status = GridSlotRecordStatus.WAITING_SELL.value
                slot.sell_order_id = order_id
            elif event.side == "Sell":
                order_id = self._order_manager.place_buy_limit(
                    event.symbol,
                    price=float(slot.buy_price),
                    qty=float(slot.units or Decimal(0)),
                )
                slot.status = GridSlotRecordStatus.WAITING_BUY.value
                slot.buy_order_id = order_id
                slot.completed_cycles += 1
                slot.realized_pnl += (slot.sell_price - slot.buy_price) * (slot.units or Decimal(0))
            await session.commit()

    async def reconcile_after_disconnect(self, symbol: str) -> list[dict[str, object]]:
        return self._order_manager.get_open_orders(symbol)

    async def _slot_for_order_id(
        self,
        session: AsyncSession,
        order_id: str,
    ) -> GridSlotRecord | None:
        statement = select(GridSlotRecord).where(
            or_(
                GridSlotRecord.buy_order_id == order_id,
                GridSlotRecord.sell_order_id == order_id,
            ),
        )
        return (await session.execute(statement)).scalar_one_or_none()
