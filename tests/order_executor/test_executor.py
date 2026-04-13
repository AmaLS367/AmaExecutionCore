from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.order_executor.executor import OrderExecutor
from backend.trade_journal.models import SignalDirection, Trade, TradeStatus


class TimeoutRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}

    def get_instruments_info(self, symbol: str, category: str = "spot") -> dict[str, object]:
        return {"lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1", "minOrderAmt": "5"}}

    def place_order(self, **_: object) -> dict[str, object]:
        raise TimeoutError("simulated timeout")

    def get_order_status(self, **_: object) -> dict[str, object] | None:
        return None


@pytest.mark.asyncio
async def test_executor_marks_pending_unknown_after_submit_timeout(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "demo"
    executor = OrderExecutor(rest_client=TimeoutRestClient())

    async with sqlite_session_factory() as session:
        trade = await executor.execute(
            session=session,
            signal_id=uuid.uuid4(),
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            entry=100.0,
            stop=90.0,
            target=130.0,
        )

        persisted_trade = (
            await session.execute(select(Trade).where(Trade.id == trade.id))
        ).scalar_one()
        assert persisted_trade.status == TradeStatus.ORDER_PENDING_UNKNOWN
