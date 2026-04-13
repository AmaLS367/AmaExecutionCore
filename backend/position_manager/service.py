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
                if exit_reason == ExitReason.SL_HIT and trade.stop_price is not None:
                    trade.avg_exit_price = trade.stop_price
                elif exit_reason == ExitReason.TP_HIT and trade.target_price is not None:
                    trade.avg_exit_price = trade.target_price
                else:
                    trade.avg_exit_price = trade.target_price or trade.entry_price or trade.stop_price
                trade.closed_at = datetime.now(UTC)
                exit_price = trade.avg_exit_price or Decimal("0")
                realized_pnl = TradeJournalStore.calculate_realized_pnl(trade, exit_price)
                trade.realized_pnl = realized_pnl
                trade.pnl_pct = TradeJournalStore.calculate_pnl_pct(trade, realized_pnl)
                trade.pnl_in_r = TradeJournalStore.calculate_pnl_in_r(trade, realized_pnl)
                if trade.opened_at is not None:
                    opened_at = trade.opened_at
                    closed_at = trade.closed_at
                    if opened_at.tzinfo is None:
                        opened_at = opened_at.replace(tzinfo=UTC)
                    if closed_at is not None and closed_at.tzinfo is None:
                        closed_at = closed_at.replace(tzinfo=UTC)
                    trade.hold_time_seconds = int(
                        (closed_at - opened_at).total_seconds()
                    )
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
