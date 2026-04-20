from __future__ import annotations

import asyncio
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.main import create_app
from backend.trade_journal.models import Signal, Trade, TradeStatus, TradingMode


class RecordingRestClient:
    def __init__(self) -> None:
        self.called = False

    def get_wallet_balance(self) -> dict[str, object]:
        self.called = True
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}


class PendingUnknownReplayRestClient:
    def __init__(self) -> None:
        self.place_order_calls = 0
        self.get_order_status_calls = 0

    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}

    def get_instruments_info(self, symbol: str, category: str = "spot") -> dict[str, object]:
        return {
            "baseCoin": "BTC",
            "quoteCoin": "USDT",
            "lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1", "minOrderAmt": "5"},
        }

    def place_order(self, **_: object) -> dict[str, object]:
        self.place_order_calls += 1
        raise TimeoutError("simulated timeout")

    def get_order_status(self, **_: object) -> dict[str, object] | None:
        self.get_order_status_calls += 1
        if self.get_order_status_calls == 1:
            return None
        return {"orderId": "resolved-1", "orderStatus": "Filled"}


def test_execute_signal_in_shadow_creates_signal_and_trade(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
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
    assert payload["replayed"] is False

    async def verify() -> None:
        async with sqlite_session_factory() as session:
            signal = (await session.execute(select(Signal))).scalar_one()
            trade = (await session.execute(select(Trade))).scalar_one()

            assert signal.symbol == "BTCUSDT"
            assert trade.signal_id == signal.id
            assert trade.status == TradeStatus.ORDER_SUBMITTED
            assert trade.mode == TradingMode.SHADOW
            assert trade.risk_amount_usd == Decimal("100.0")

    asyncio.run(verify())


def test_execute_signal_replays_identical_shadow_request(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    app = create_app(session_factory=sqlite_session_factory, rest_client=RecordingRestClient())
    request_body = {
        "symbol": " btcusdt ",
        "direction": "long",
        "entry": 100.0,
        "stop": 90.0,
        "target": 130.0,
        "reason": " shadow-test ",
        "strategy_version": " v-test ",
        "indicators_snapshot": {"slow": 21, "fast": 9},
    }

    with TestClient(app) as client:
        first_response = client.post("/signals/execute", json=request_body)
        second_response = client.post("/signals/execute", json=request_body)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_payload = first_response.json()
    second_payload = second_response.json()
    assert first_payload["replayed"] is False
    assert second_payload["replayed"] is True
    assert second_payload["signal_id"] == first_payload["signal_id"]
    assert second_payload["trade_id"] == first_payload["trade_id"]

    async def verify() -> None:
        async with sqlite_session_factory() as session:
            signals = (await session.execute(select(Signal))).scalars().all()
            trades = (await session.execute(select(Trade))).scalars().all()

            assert len(signals) == 1
            assert len(trades) == 1

    asyncio.run(verify())


def test_execute_signal_replays_pending_unknown_without_resubmission(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "demo"
    rest_client = PendingUnknownReplayRestClient()
    app = create_app(session_factory=sqlite_session_factory, rest_client=rest_client)
    request_body = {
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 100.0,
        "stop": 90.0,
        "target": 130.0,
    }

    with TestClient(app) as client:
        first_response = client.post("/signals/execute", json=request_body)
        second_response = client.post("/signals/execute", json=request_body)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_payload = first_response.json()
    second_payload = second_response.json()
    assert first_payload["status"] == TradeStatus.ORDER_PENDING_UNKNOWN.value
    assert first_payload["replayed"] is False
    assert second_payload["status"] == TradeStatus.ORDER_CONFIRMED.value
    assert second_payload["replayed"] is True
    assert second_payload["signal_id"] == first_payload["signal_id"]
    assert second_payload["trade_id"] == first_payload["trade_id"]
    assert rest_client.place_order_calls == 1
    assert rest_client.get_order_status_calls == 2

    async def verify() -> None:
        async with sqlite_session_factory() as session:
            signals = (await session.execute(select(Signal))).scalars().all()
            trade = (await session.execute(select(Trade))).scalar_one()

            assert len(signals) == 1
            assert trade.status == TradeStatus.ORDER_CONFIRMED
            assert trade.exchange_order_id == "resolved-1"

    asyncio.run(verify())


def test_execute_signal_allows_new_execution_after_terminal_trade(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    app = create_app(session_factory=sqlite_session_factory, rest_client=RecordingRestClient())
    request_body = {
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 100.0,
        "stop": 90.0,
        "target": 130.0,
    }

    with TestClient(app) as client:
        first_response = client.post("/signals/execute", json=request_body)

        async def mark_terminal() -> None:
            async with sqlite_session_factory() as session:
                trade = (await session.execute(select(Trade))).scalar_one()
                trade.status = TradeStatus.POSITION_CLOSED
                await session.commit()

        asyncio.run(mark_terminal())
        second_response = client.post("/signals/execute", json=request_body)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_payload = first_response.json()
    second_payload = second_response.json()
    assert first_payload["replayed"] is False
    assert second_payload["replayed"] is False
    assert second_payload["signal_id"] != first_payload["signal_id"]
    assert second_payload["trade_id"] != first_payload["trade_id"]

    async def verify() -> None:
        async with sqlite_session_factory() as session:
            signals = (await session.execute(select(Signal))).scalars().all()
            trades = (await session.execute(select(Trade))).scalars().all()

            assert len(signals) == 2
            assert len(trades) == 2

    asyncio.run(verify())
