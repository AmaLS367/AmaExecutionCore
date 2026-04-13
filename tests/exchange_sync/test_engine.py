from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.exchange_sync.engine import ExchangeSyncEngine
from backend.exchange_sync.listener import BybitWebSocketListener
from backend.trade_journal.models import (
    ExchangeSide,
    ExitReason,
    MarketType,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)


@pytest.mark.asyncio
async def test_exchange_sync_records_close_and_pnl(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory)

    async with sqlite_session_factory() as session:
        trade = Trade(
            signal_id=uuid.uuid4(),
            order_link_id="entry-1",
            close_order_link_id="close-1",
            symbol="BTCUSDT",
            signal_direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            market_type=MarketType.SPOT,
            mode=TradingMode.DEMO,
            entry_price=Decimal("100"),
            avg_fill_price=Decimal("100"),
            filled_qty=Decimal("1"),
            qty=Decimal("1"),
            risk_amount_usd=Decimal("10"),
            risk_pct=Decimal("0.01"),
            status=TradeStatus.POSITION_CLOSE_PENDING,
            opened_at=datetime.now(timezone.utc),
        )
        session.add(trade)
        await session.commit()

    await engine._process_order(  # noqa: SLF001
        {
            "orderLinkId": "close-1",
            "orderStatus": "Filled",
            "avgPrice": "110",
            "cumExecQty": "1",
            "leavesQty": "0",
        }
    )

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()
        assert persisted_trade.status == TradeStatus.PNL_RECORDED
        assert persisted_trade.exit_reason == ExitReason.MANUAL
        assert persisted_trade.realized_pnl == Decimal("10")


@pytest.mark.asyncio
async def test_exchange_sync_wire_uses_running_loop(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory)
    listener = BybitWebSocketListener()

    engine.wire(listener)

    assert engine._loop is not None  # noqa: SLF001
    assert engine._loop.is_running()  # noqa: SLF001
