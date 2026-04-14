from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.trade_journal.models import DailyStat, SafetyState, Trade, TradeStatus


def test_execute_signal_replays_on_migrated_postgresql_schema(
    postgresql_client: TestClient,
    postgresql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    request_body = {
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 100.0,
        "stop": 90.0,
        "target": 130.0,
        "reason": "postgres-api-test",
        "strategy_version": "postgres-v1",
    }

    first_response = postgresql_client.post("/signals/execute", json=request_body)
    second_response = postgresql_client.post("/signals/execute", json=request_body)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first_payload = first_response.json()
    second_payload = second_response.json()
    assert first_payload["replayed"] is False
    assert second_payload["replayed"] is True
    assert second_payload["signal_id"] == first_payload["signal_id"]
    assert second_payload["trade_id"] == first_payload["trade_id"]

    async def verify() -> None:
        async with postgresql_session_factory() as session:
            trades = (await session.execute(select(Trade))).scalars().all()
            assert len(trades) == 1
            assert trades[0].status == TradeStatus.ORDER_SUBMITTED

    asyncio.run(verify())


def test_safety_endpoints_persist_state_on_migrated_postgresql_schema(
    postgresql_client: TestClient,
    postgresql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    kill_response = postgresql_client.post("/safety/kill")
    status_response = postgresql_client.get("/safety/status")
    reset_response = postgresql_client.post("/safety/reset")

    assert kill_response.status_code == 200
    assert status_response.status_code == 200
    assert reset_response.status_code == 200
    assert status_response.json()["kill_switch"] is True
    assert reset_response.json()["kill_switch"] is False

    async def verify() -> None:
        async with postgresql_session_factory() as session:
            state = (await session.execute(select(SafetyState))).scalar_one()
            assert state.kill_switch_active is False
            assert state.pause_reason is None

    asyncio.run(verify())


def test_shadow_trade_lifecycle_api_flow_on_migrated_postgresql_schema(
    postgresql_client: TestClient,
    postgresql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    execute_response = postgresql_client.post(
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
    trade_id = UUID(execute_response.json()["trade_id"])

    async def promote_trade() -> None:
        async with postgresql_session_factory() as session:
            trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()
            trade.status = TradeStatus.POSITION_OPEN
            trade.opened_at = datetime.now(UTC)
            trade.avg_fill_price = trade.entry_price
            trade.filled_qty = trade.qty
            await session.commit()

    asyncio.run(promote_trade())

    close_response = postgresql_client.post(f"/positions/{trade_id}/close")
    detail_response = postgresql_client.get(f"/trades/{trade_id}")

    assert close_response.status_code == 200
    assert detail_response.status_code == 200
    assert close_response.json()["status"] == TradeStatus.PNL_RECORDED.value
    assert detail_response.json()["status"] == TradeStatus.PNL_RECORDED.value

    async def verify() -> None:
        async with postgresql_session_factory() as session:
            trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()
            daily_stat = (await session.execute(select(DailyStat))).scalar_one()
            assert trade.status == TradeStatus.PNL_RECORDED
            assert daily_stat.total_trades == 1

    asyncio.run(verify())
