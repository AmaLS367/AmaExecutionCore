from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.grid_engine.grid_ws_handler import GridOrderFillEvent, GridWebSocketHandler
from backend.grid_engine.models import GridSession, GridSlotRecord, GridSlotRecordStatus


class RecordingOrderManager:
    def __init__(self) -> None:
        self.sell_calls: list[tuple[str, float, float]] = []
        self.buy_calls: list[tuple[str, float, float]] = []
        self.open_order_calls: list[str] = []

    def place_sell_limit(self, symbol: str, price: float, qty: float) -> str:
        self.sell_calls.append((symbol, price, qty))
        return "sell-next"

    def place_buy_limit(self, symbol: str, price: float, qty: float) -> str:
        self.buy_calls.append((symbol, price, qty))
        return "buy-next"

    def get_open_orders(self, symbol: str) -> list[dict[str, object]]:
        self.open_order_calls.append(symbol)
        return [{"orderId": "open-1"}]


@pytest.mark.asyncio
async def test_buy_fill_places_matching_sell(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    order_manager = RecordingOrderManager()
    handler = GridWebSocketHandler(
        session_factory=sqlite_session_factory,
        order_manager=order_manager,  # type: ignore[arg-type]
    )
    await _seed_slot(sqlite_session_factory, buy_order_id="buy-1", sell_order_id=None)

    await handler.handle_order_fill(GridOrderFillEvent(order_id="buy-1", side="Buy", symbol="XRPUSDT"))

    assert order_manager.sell_calls == [("XRPUSDT", 1.84, 2.0)]
    async with sqlite_session_factory() as session:
        slot = (await session.execute(select(GridSlotRecord))).scalar_one()
        assert slot.status == GridSlotRecordStatus.WAITING_SELL.value
        assert slot.sell_order_id == "sell-next"


@pytest.mark.asyncio
async def test_sell_fill_places_matching_buy_and_counts_cycle(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    order_manager = RecordingOrderManager()
    handler = GridWebSocketHandler(
        session_factory=sqlite_session_factory,
        order_manager=order_manager,  # type: ignore[arg-type]
    )
    await _seed_slot(sqlite_session_factory, buy_order_id=None, sell_order_id="sell-1")

    await handler.handle_order_fill(
        GridOrderFillEvent(order_id="sell-1", side="Sell", symbol="XRPUSDT"),
    )

    assert order_manager.buy_calls == [("XRPUSDT", 1.8, 2.0)]
    async with sqlite_session_factory() as session:
        slot = (await session.execute(select(GridSlotRecord))).scalar_one()
        assert slot.status == GridSlotRecordStatus.WAITING_BUY.value
        assert slot.buy_order_id == "buy-next"
        assert slot.completed_cycles == 1


@pytest.mark.asyncio
async def test_disconnect_reconciliation_fetches_open_orders(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    order_manager = RecordingOrderManager()
    handler = GridWebSocketHandler(
        session_factory=sqlite_session_factory,
        order_manager=order_manager,  # type: ignore[arg-type]
    )

    result = await handler.reconcile_after_disconnect("XRPUSDT")

    assert result == [{"orderId": "open-1"}]
    assert order_manager.open_order_calls == ["XRPUSDT"]


async def _seed_slot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    buy_order_id: str | None,
    sell_order_id: str | None,
) -> None:
    async with session_factory() as session:
        grid_session = GridSession(
            symbol="XRPUSDT",
            config_json={},
            status="active",
        )
        grid_session.slots = [
            GridSlotRecord(
                level=0,
                buy_price=Decimal("1.8"),
                sell_price=Decimal("1.84"),
                status=GridSlotRecordStatus.WAITING_BUY.value,
                completed_cycles=0,
                realized_pnl=Decimal(0),
                units=Decimal(2),
                buy_order_id=buy_order_id,
                sell_order_id=sell_order_id,
            ),
        ]
        session.add(grid_session)
        await session.commit()
