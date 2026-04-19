from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.main import create_app
from backend.order_executor.executor import OrderAlreadySubmittedError
from backend.risk_manager.exceptions import BelowMinQtyError
from backend.safety_guard.exceptions import SafetyGuardError
from backend.trade_journal.models import (
    ExchangeSide,
    MarketType,
    PauseReason,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)
from backend.trade_journal.store import PersistedSafetyState


class _PassiveRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}

    def get_instruments_info(self, symbol: str, category: str = "spot") -> dict[str, object]:
        return {"lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1", "minOrderAmt": "5"}}

    def place_order(self, **_: object) -> dict[str, object]:
        return {"orderId": "unused"}


class _RaisingExecutionService:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def execute_signal(self, *, signal: object) -> object:
        raise self.exc


class _RaisingPositionManager:
    def __init__(self, message: str) -> None:
        self.message = message

    async def close_trade(self, *, trade_id: object, exit_reason: object = None) -> Trade:
        del trade_id, exit_reason
        raise ValueError(self.message)

    async def list_open_trades(self) -> list[Trade]:
        return []


def _build_trade(*, mode: TradingMode, status: TradeStatus) -> Trade:
    return Trade(
        signal_id=uuid4(),
        order_link_id=f"trade-{uuid4().hex[:8]}",
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
        opened_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (OrderAlreadySubmittedError("duplicate"), 409),
        (SafetyGuardError("blocked"), 423),
        (BelowMinQtyError("too small"), 422),
    ],
)
def test_execute_signal_maps_domain_errors_to_http_statuses(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    exc: Exception,
    expected_status: int,
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=_PassiveRestClient())
    app.state.execution_service = _RaisingExecutionService(exc)

    with TestClient(app) as client:
        response = client.post(
            "/signals/execute",
            json={"symbol": "BTCUSDT", "direction": "long", "entry": 100, "stop": 90, "target": 130},
        )

    assert response.status_code == expected_status
    assert response.json()["detail"] == str(exc)


@pytest.mark.parametrize(
    ("message", "expected_status"),
    [
        ("Trade not found", 404),
        ("Trade is not open", 409),
        ("Bad request", 400),
    ],
)
def test_close_position_maps_value_errors(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    message: str,
    expected_status: int,
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=_PassiveRestClient())
    app.state.position_manager = _RaisingPositionManager(message)

    with TestClient(app) as client:
        response = client.post(f"/positions/{uuid4()}/close")

    assert response.status_code == expected_status


def test_list_trades_filters_by_mode(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=_PassiveRestClient())

    async def seed() -> None:
        async with sqlite_session_factory() as session:
            session.add_all(
                [
                    _build_trade(mode=TradingMode.SHADOW, status=TradeStatus.ORDER_SUBMITTED),
                    _build_trade(mode=TradingMode.DEMO, status=TradeStatus.POSITION_OPEN),
                ]
            )
            await session.commit()

    asyncio.run(seed())

    with TestClient(app) as client:
        response = client.get("/trades", params={"mode": TradingMode.DEMO.value})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["mode"] == TradingMode.DEMO.value


def test_get_trade_detail_returns_404_for_unknown_trade(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=_PassiveRestClient())

    with TestClient(app) as client:
        response = client.get(f"/trades/{uuid4()}")

    assert response.status_code == 404


def test_safety_routes_return_serialized_state(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(session_factory=sqlite_session_factory, rest_client=_PassiveRestClient())
    seen_rest_clients: list[object] = []

    async def _activate(*, session: AsyncSession, rest_client: object | None = None) -> None:
        del session
        seen_rest_clients.append(rest_client)

    async def _reset(session: AsyncSession) -> PersistedSafetyState:
        del session
        return PersistedSafetyState(
            kill_switch_active=False,
            pause_reason=None,
            cooldown_until=None,
            manual_reset_required=False,
        )

    async def _status(session: AsyncSession) -> PersistedSafetyState:
        del session
        return PersistedSafetyState(
            kill_switch_active=True,
            pause_reason=PauseReason.COOLDOWN,
            cooldown_until=datetime(2024, 1, 1, tzinfo=UTC),
            manual_reset_required=True,
        )

    monkeypatch.setattr("backend.safety_guard.router.kill_switch.activate", _activate)
    monkeypatch.setattr("backend.safety_guard.router.kill_switch.reset", _reset)
    monkeypatch.setattr("backend.safety_guard.router.kill_switch.status", _status)

    with TestClient(app) as client:
        kill_response = client.post("/safety/kill")
        reset_response = client.post("/safety/reset")
        status_response = client.get("/safety/status")

    assert kill_response.status_code == 200
    assert reset_response.json()["kill_switch"] is False
    assert status_response.json()["kill_switch"] is True
    assert status_response.json()["pause_reason"] == PauseReason.COOLDOWN.value
    assert seen_rest_clients == [app.state.rest_client]
