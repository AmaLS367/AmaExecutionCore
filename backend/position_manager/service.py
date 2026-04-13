from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.trade_journal.models import ExchangeSide, ExitReason, Trade, TradeStatus
from backend.trade_journal.store import TradeJournalStore


class PositionManagerService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        rest_client: Any,
    ) -> None:
        self._session_factory = session_factory
        self._rest_client = rest_client

    async def close_trade(
        self,
        *,
        trade_id: uuid.UUID,
        exit_reason: ExitReason = ExitReason.MANUAL,
    ) -> Trade:
        async with self._session_factory() as session:
            store = TradeJournalStore(session)
            trade = await store.get_trade(trade_id)
            if trade is None:
                raise ValueError(f"Trade {trade_id} was not found.")
            if trade.status not in {TradeStatus.POSITION_OPEN, TradeStatus.ORDER_PARTIALLY_FILLED}:
                raise ValueError(f"Trade {trade_id} is not open.")

            close_order_link_id = f"close_{uuid.uuid4().hex[:12]}"
            close_side = ExchangeSide.SELL if trade.exchange_side == ExchangeSide.BUY else ExchangeSide.BUY
            qty = trade.filled_qty or trade.qty or Decimal("0")

            trade.close_order_link_id = close_order_link_id
            trade.exit_reason = exit_reason
            trade.status = TradeStatus.POSITION_CLOSE_PENDING

            if settings.trading_mode == "shadow":
                trade.avg_exit_price = trade.target_price or trade.entry_price
                trade.closed_at = datetime.now(UTC)
                trade.status = TradeStatus.PNL_RECORDED
                await session.commit()
                return trade

            result = await asyncio.to_thread(
                self._rest_client.place_order,
                category="spot",
                symbol=trade.symbol,
                side=close_side.value,
                order_type="Market",
                qty=str(qty),
                order_link_id=close_order_link_id,
                market_unit="baseCoin" if close_side == ExchangeSide.BUY else None,
            )
            trade.close_exchange_order_id = result.get("orderId")
            await session.commit()
            return trade
