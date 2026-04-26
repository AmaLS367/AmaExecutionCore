from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.grid_engine.grid_runner import GridRunner
from backend.grid_engine.models import GridSession, GridSessionStatus, GridSlotRecord


class RecordingRiskOrderManager:
    def __init__(self) -> None:
        self.cancel_all_calls: list[str] = []

    def place_buy_limit(self, symbol: str, price: float, qty: float) -> str:
        return f"buy-{symbol}-{price}-{qty}"

    def place_sell_limit(self, symbol: str, price: float, qty: float) -> str:
        return f"sell-{symbol}-{price}-{qty}"

    def cancel_all_orders(self, symbol: str) -> int:
        self.cancel_all_calls.append(symbol)
        return 1


class StaticRiskPriceClient:
    def get_ticker_price(self, symbol: str, category: str = "spot") -> float:
        del symbol, category
        return 1.0


@pytest.mark.asyncio
async def test_grid_risk_pauses_when_unrealized_drawdown_exceeds_limit(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id = await _seed_risk_session(
        sqlite_session_factory,
        slot_status="waiting_sell",
        buy_price=Decimal("1.0"),
        units=Decimal(20),
    )
    runner = GridRunner(
        session_factory=sqlite_session_factory,
        order_manager=RecordingRiskOrderManager(),  # type: ignore[arg-type]
        rest_client=StaticRiskPriceClient(),
    )

    await runner.evaluate_risk(session_id, current_price=0.64, config_max_dd_pct=35.0)

    async with sqlite_session_factory() as session:
        grid_session = (await session.execute(select(GridSession))).scalar_one()
        assert grid_session.status == GridSessionStatus.PAUSED.value


@pytest.mark.asyncio
async def test_grid_upside_breakout_sets_waiting_reentry(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id = await _seed_risk_session(
        sqlite_session_factory,
        slot_status="waiting_buy",
        buy_price=Decimal("1.0"),
        units=Decimal(20),
    )
    runner = GridRunner(
        session_factory=sqlite_session_factory,
        order_manager=RecordingRiskOrderManager(),  # type: ignore[arg-type]
        rest_client=StaticRiskPriceClient(),
    )

    handled = await runner.handle_upside_breakout(session_id)

    assert handled is True
    async with sqlite_session_factory() as session:
        grid_session = (await session.execute(select(GridSession))).scalar_one()
        assert grid_session.status == GridSessionStatus.WAITING_REENTRY.value


async def _seed_risk_session(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    slot_status: str,
    buy_price: Decimal,
    units: Decimal,
) -> int:
    async with session_factory() as session:
        grid_session = GridSession(
            symbol="XRPUSDT",
            config_json={
                "symbol": "XRPUSDT",
                "p_min": 1.0,
                "p_max": 1.2,
                "n_levels": 1,
                "capital_usdt": 20.0,
            },
            status="active",
        )
        grid_session.slots = [
            GridSlotRecord(
                level=0,
                buy_price=buy_price,
                sell_price=Decimal("1.2"),
                status=slot_status,
                completed_cycles=0,
                realized_pnl=Decimal(0),
                units=units,
            ),
        ]
        session.add(grid_session)
        await session.commit()
        return grid_session.id
