from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.grid_engine.grid_runner import GridRunner
from backend.grid_engine.grid_ws_handler import GridOrderFillEvent
from backend.grid_engine.models import GridSession, GridSlotRecord, GridSlotRecordStatus


class RecordingRunnerOrderManager:
    def __init__(self) -> None:
        self.buy_calls: list[tuple[str, float, float]] = []
        self.sell_calls: list[tuple[str, float, float]] = []
        self.cancel_all_calls: list[str] = []

    def place_buy_limit(self, symbol: str, price: float, qty: float) -> str:
        self.buy_calls.append((symbol, price, qty))
        return f"buy-{len(self.buy_calls)}"

    def place_sell_limit(self, symbol: str, price: float, qty: float) -> str:
        self.sell_calls.append((symbol, price, qty))
        return f"sell-{len(self.sell_calls)}"

    def cancel_all_orders(self, symbol: str) -> int:
        self.cancel_all_calls.append(symbol)
        return 1


class StaticPriceRestClient:
    def get_ticker_price(self, symbol: str, category: str = "spot") -> float:
        assert symbol == "XRPUSDT"
        assert category == "spot"
        return 1.85


@pytest.mark.asyncio
async def test_grid_runner_start_places_waiting_buy_orders(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id = await _seed_grid_session(sqlite_session_factory)
    order_manager = RecordingRunnerOrderManager()
    runner = GridRunner(
        session_factory=sqlite_session_factory,
        order_manager=order_manager,  # type: ignore[arg-type]
        rest_client=StaticPriceRestClient(),
    )

    await runner.start(session_id)

    assert order_manager.buy_calls == [
        ("XRPUSDT", 1.8, 2.7777777777777777),
        ("XRPUSDT", 1.84, 2.717391304347826),
    ]


@pytest.mark.asyncio
async def test_grid_runner_fill_delegates_to_handler_and_places_sell(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id = await _seed_grid_session(sqlite_session_factory)
    order_manager = RecordingRunnerOrderManager()
    runner = GridRunner(
        session_factory=sqlite_session_factory,
        order_manager=order_manager,  # type: ignore[arg-type]
        rest_client=StaticPriceRestClient(),
    )
    await runner.start(session_id)

    await runner.handle_order_fill(GridOrderFillEvent(order_id="buy-1", side="Buy", symbol="XRPUSDT"))

    assert order_manager.sell_calls == [("XRPUSDT", 1.84, 2.77777778)]


@pytest.mark.asyncio
async def test_grid_runner_stop_cancels_all_orders(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    session_id = await _seed_grid_session(sqlite_session_factory)
    order_manager = RecordingRunnerOrderManager()
    runner = GridRunner(
        session_factory=sqlite_session_factory,
        order_manager=order_manager,  # type: ignore[arg-type]
        rest_client=StaticPriceRestClient(),
    )

    await runner.stop(session_id)

    assert order_manager.cancel_all_calls == ["XRPUSDT"]


async def _seed_grid_session(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        grid_session = GridSession(
            symbol="XRPUSDT",
            config_json={
                "symbol": "XRPUSDT",
                "p_min": 1.8,
                "p_max": 1.88,
                "n_levels": 2,
                "capital_usdt": 10.0,
            },
            status="paused",
        )
        grid_session.slots = [
            GridSlotRecord(
                level=0,
                buy_price=Decimal("1.8"),
                sell_price=Decimal("1.84"),
                status=GridSlotRecordStatus.WAITING_BUY.value,
                completed_cycles=0,
                realized_pnl=Decimal(0),
            ),
            GridSlotRecord(
                level=1,
                buy_price=Decimal("1.84"),
                sell_price=Decimal("1.88"),
                status=GridSlotRecordStatus.WAITING_BUY.value,
                completed_cycles=0,
                realized_pnl=Decimal(0),
            ),
        ]
        session.add(grid_session)
        await session.commit()
        return grid_session.id
