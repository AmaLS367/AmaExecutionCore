from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.main import create_app
from backend.trade_journal.models import (
    ExchangeSide,
    MarketType,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)


class PassiveRestClient:
    def place_order(self, **_: object) -> dict[str, object]:
        return {"orderId": "unused"}

    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}

    def get_instruments_info(self, symbol: str, category: str = "spot") -> dict[str, object]:
        return {
            "baseCoin": "BTC",
            "quoteCoin": "USDT",
            "lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1", "minOrderAmt": "5"},
        }


def build_trade(
    *,
    status: TradeStatus,
    mode: TradingMode = TradingMode.SHADOW,
    created_at: datetime | None = None,
) -> Trade:
    return Trade(
        signal_id=uuid.uuid4(),
        order_link_id=f"trade-{uuid.uuid4().hex[:8]}",
        symbol="BTCUSDT",
        signal_direction=SignalDirection.LONG,
        exchange_side=ExchangeSide.BUY,
        market_type=MarketType.SPOT,
        mode=mode,
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("130"),
        qty=Decimal("1"),
        filled_qty=Decimal("1"),
        status=status,
        opened_at=datetime.now(UTC) - timedelta(minutes=1),
        created_at=created_at,
    )


def test_get_open_positions_returns_empty_list(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=PassiveRestClient())

    with TestClient(app) as client:
        response = client.get("/positions/open")

    assert response.status_code == 200
    assert response.json() == []


def test_get_open_positions_returns_shadow_trade_after_execution(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    app = create_app(session_factory=sqlite_session_factory, rest_client=PassiveRestClient())

    with TestClient(app) as client:
        execute_response = client.post(
            "/signals/execute",
            json={
                "symbol": "BTCUSDT",
                "direction": "long",
                "entry": 100.0,
                "stop": 90.0,
                "target": 130.0,
            },
        )
        assert execute_response.status_code == 200
        trade_id = execute_response.json()["trade_id"]

        async def promote_trade() -> None:
            async with sqlite_session_factory() as session:
                trade = (
                    await session.execute(select(Trade).where(Trade.id == uuid.UUID(trade_id)))
                ).scalar_one()
                trade.status = TradeStatus.POSITION_OPEN
                trade.opened_at = datetime.now(UTC)
                await session.commit()

        asyncio.run(promote_trade())

        response = client.get("/positions/open")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["trade_id"] == trade_id


def test_close_position_returns_404_for_missing_trade(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=PassiveRestClient())

    with TestClient(app) as client:
        response = client.post(f"/positions/{uuid.uuid4()}/close")

    assert response.status_code == 404


def test_get_trades_supports_pagination(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=PassiveRestClient())

    async def seed() -> None:
        async with sqlite_session_factory() as session:
            session.add_all(
                [
                    build_trade(
                        status=TradeStatus.PNL_RECORDED,
                        created_at=datetime.now(UTC) - timedelta(minutes=3),
                    ),
                    build_trade(
                        status=TradeStatus.POSITION_OPEN,
                        created_at=datetime.now(UTC) - timedelta(minutes=2),
                    ),
                    build_trade(
                        status=TradeStatus.ORDER_SUBMITTED,
                        created_at=datetime.now(UTC) - timedelta(minutes=1),
                    ),
                ]
            )
            await session.commit()

    asyncio.run(seed())

    with TestClient(app) as client:
        response = client.get("/trades", params={"limit": 2, "offset": 1})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["status"] == TradeStatus.POSITION_OPEN.value
    assert payload[1]["status"] == TradeStatus.PNL_RECORDED.value
