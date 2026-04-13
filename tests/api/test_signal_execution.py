from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.main import create_app
from backend.trade_journal.models import Signal, Trade, TradeStatus, TradingMode


class RecordingRestClient:
    def __init__(self) -> None:
        self.called = False

    def get_wallet_balance(self) -> dict[str, object]:
        self.called = True
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}


def test_execute_signal_in_shadow_creates_signal_and_trade(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=RecordingRestClient())

    with TestClient(app) as client:
        response = client.post(
            "/signals/execute",
            json={
                "symbol": "BTCUSDT",
                "direction": "long",
                "entry": 100.0,
                "stop": 90.0,
                "target": 130.0,
                "reason": "shadow-test",
                "strategy_version": "v-test",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == TradeStatus.ORDER_SUBMITTED.value
    assert payload["mode"] == TradingMode.SHADOW.value

    async def verify() -> None:
        async with sqlite_session_factory() as session:
            signal = (await session.execute(select(Signal))).scalar_one()
            trade = (await session.execute(select(Trade))).scalar_one()

            assert signal.symbol == "BTCUSDT"
            assert trade.signal_id == signal.id
            assert trade.status == TradeStatus.ORDER_SUBMITTED
            assert trade.mode == TradingMode.SHADOW
            assert trade.risk_amount_usd == Decimal("100.0")

    import asyncio

    asyncio.run(verify())
