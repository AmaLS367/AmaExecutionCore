from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.safety_guard.kill_switch import KillSwitch
from backend.trade_journal.models import (
    ExchangeSide,
    MarketType,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)


class RecordingRestClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def cancel_order(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return {"retCode": 0}


@pytest.mark.asyncio
async def test_kill_switch_cancels_pending_orders(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "demo"
    kill_switch = KillSwitch()
    rest_client = RecordingRestClient()

    async with sqlite_session_factory() as session:
        session.add(
            Trade(
                signal_id=uuid.uuid4(),
                order_link_id="pending-1",
                symbol="BTCUSDT",
                signal_direction=SignalDirection.LONG,
                exchange_side=ExchangeSide.BUY,
                market_type=MarketType.SPOT,
                mode=TradingMode.DEMO,
                status=TradeStatus.ORDER_SUBMITTED,
            )
        )
        await session.commit()

        await kill_switch.activate(session, rest_client=rest_client)

    assert rest_client.calls == [
        {
            "category": "spot",
            "symbol": "BTCUSDT",
            "order_link_id": "pending-1",
        }
    ]
