from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.bybit_client.exceptions import BybitAPIError
from backend.config import settings
from backend.exchange_sync.engine import ExchangeSyncEngine
from backend.exchange_sync.listener import BybitWebSocketListener
from backend.trade_journal.models import (
    ExchangeSide,
    ExitReason,
    SafetyState,
    SystemEvent,
    MarketType,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)


class ProtectionRestClient:
    def __init__(self) -> None:
        self.place_order_calls: list[dict[str, object]] = []
        self.cancel_order_calls: list[dict[str, object]] = []

    def place_order(self, **kwargs: object) -> dict[str, object]:
        self.place_order_calls.append(dict(kwargs))
        return {"orderId": f"exchange-{len(self.place_order_calls)}"}

    def cancel_order(self, **kwargs: object) -> dict[str, object]:
        self.cancel_order_calls.append(dict(kwargs))
        return {"orderId": kwargs.get("order_id"), "orderLinkId": kwargs.get("order_link_id")}

    def get_order_status(self, **_: object) -> dict[str, object] | None:
        return None


class StopLossFailureRestClient(ProtectionRestClient):
    def place_order(self, **kwargs: object) -> dict[str, object]:
        self.place_order_calls.append(dict(kwargs))
        if kwargs.get("order_filter") == "tpslOrder":
            raise BybitAPIError(170001, "stop arm failed")
        return {"orderId": f"exchange-{len(self.place_order_calls)}"}


@pytest.mark.asyncio
async def test_exchange_sync_records_close_and_pnl(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory)

    async with sqlite_session_factory() as session:
        trade = Trade(
            signal_id=uuid.uuid4(),
            order_link_id="entry-1",
            close_order_link_id="close-1",
            symbol="BTCUSDT",
            signal_direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            market_type=MarketType.SPOT,
            mode=TradingMode.DEMO,
            entry_price=Decimal("100"),
            avg_fill_price=Decimal("100"),
            filled_qty=Decimal("1"),
            qty=Decimal("1"),
            risk_amount_usd=Decimal("10"),
            risk_pct=Decimal("0.01"),
            status=TradeStatus.POSITION_CLOSE_PENDING,
            opened_at=datetime.now(timezone.utc),
        )
        session.add(trade)
        await session.commit()

    await engine._process_order(  # noqa: SLF001
        {
            "orderLinkId": "close-1",
            "orderStatus": "Filled",
            "avgPrice": "110",
            "cumExecQty": "1",
            "leavesQty": "0",
        }
    )

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()
        assert persisted_trade.status == TradeStatus.PNL_RECORDED
        assert persisted_trade.exit_reason == ExitReason.MANUAL
        assert persisted_trade.realized_pnl == Decimal("10")


@pytest.mark.asyncio
async def test_exchange_sync_wire_uses_running_loop(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory)
    listener = BybitWebSocketListener()

    engine.wire(listener)

    assert engine._loop is not None  # noqa: SLF001
    assert engine._loop.is_running()  # noqa: SLF001


@pytest.mark.asyncio
async def test_exchange_sync_reconciliation_worker_uses_logged_task_helper(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.trading_mode = "demo"
    recorded_task_names: list[str] = []

    def _fake_create_logged_task(coroutine: object, *, name: str) -> asyncio.Task[None]:
        recorded_task_names.append(name)
        typed_coroutine = coroutine
        assert asyncio.iscoroutine(typed_coroutine)
        typed_coroutine.close()
        return asyncio.create_task(asyncio.sleep(0), name=name)

    monkeypatch.setattr("backend.exchange_sync.engine.create_logged_task", _fake_create_logged_task)

    engine = ExchangeSyncEngine(
        session_factory=sqlite_session_factory,
        rest_client=ProtectionRestClient(),
    )

    engine.start_reconciliation_worker()
    await asyncio.gather(engine._reconciliation_task, return_exceptions=True)  # type: ignore[arg-type]

    assert recorded_task_names == ["exchange-sync-reconciliation"]


def _build_entry_trade(
    *,
    status: TradeStatus = TradeStatus.ORDER_SUBMITTED,
    order_type: str | None = None,
    target_price: Decimal | None = Decimal("130"),
) -> Trade:
    return Trade(
        signal_id=uuid.uuid4(),
        order_link_id="entry-1",
        symbol="BTCUSDT",
        signal_direction=SignalDirection.LONG,
        exchange_side=ExchangeSide.BUY,
        market_type=MarketType.SPOT,
        mode=TradingMode.DEMO,
        entry_price=Decimal("100"),
        avg_fill_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=target_price,
        filled_qty=Decimal("1"),
        qty=Decimal("1"),
        risk_amount_usd=Decimal("10"),
        risk_pct=Decimal("0.01"),
        status=status,
        opened_at=datetime.now(timezone.utc),
        order_type=order_type,
    )


def _build_close_trade(*, status: TradeStatus = TradeStatus.POSITION_CLOSE_PENDING) -> Trade:
    return Trade(
        signal_id=uuid.uuid4(),
        order_link_id="entry-2",
        close_order_link_id="close-1",
        symbol="BTCUSDT",
        signal_direction=SignalDirection.LONG,
        exchange_side=ExchangeSide.BUY,
        market_type=MarketType.SPOT,
        mode=TradingMode.DEMO,
        entry_price=Decimal("100"),
        avg_fill_price=Decimal("100"),
        filled_qty=Decimal("1"),
        qty=Decimal("1"),
        risk_amount_usd=Decimal("10"),
        risk_pct=Decimal("0.01"),
        realized_pnl=Decimal("10") if status == TradeStatus.PNL_RECORDED else None,
        avg_exit_price=Decimal("110") if status == TradeStatus.PNL_RECORDED else None,
        status=status,
        opened_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc) if status == TradeStatus.PNL_RECORDED else None,
        exit_reason=ExitReason.MANUAL if status == TradeStatus.PNL_RECORDED else None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("late_status", ["PartiallyFilled", "Cancelled", "Rejected"])
async def test_exchange_sync_ignores_stale_entry_updates_after_position_is_open(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    late_status: str,
) -> None:
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory)

    async with sqlite_session_factory() as session:
        trade = _build_entry_trade(status=TradeStatus.POSITION_OPEN)
        session.add(trade)
        await session.commit()

    await engine._process_order(  # noqa: SLF001
        {
            "orderLinkId": "entry-1",
            "orderStatus": late_status,
            "avgPrice": "100",
            "cumExecQty": "1",
            "leavesQty": "0" if late_status != "PartiallyFilled" else "0.5",
        }
    )

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()

    assert persisted_trade.status == TradeStatus.POSITION_OPEN


@pytest.mark.asyncio
@pytest.mark.parametrize("late_status", ["PartiallyFilled", "Cancelled", "Rejected"])
async def test_exchange_sync_ignores_stale_close_updates_after_pnl_is_recorded(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    late_status: str,
) -> None:
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory)

    async with sqlite_session_factory() as session:
        trade = _build_close_trade(status=TradeStatus.PNL_RECORDED)
        session.add(trade)
        await session.commit()

    await engine._process_order(  # noqa: SLF001
        {
            "orderLinkId": "close-1",
            "orderStatus": late_status,
            "avgPrice": "110",
            "cumExecQty": "1",
            "leavesQty": "0" if late_status != "PartiallyFilled" else "0.5",
        }
    )

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()
        system_events = (await session.execute(select(SystemEvent))).scalars().all()

    assert persisted_trade.status == TradeStatus.PNL_RECORDED
    assert system_events == []


@pytest.mark.asyncio
async def test_exchange_sync_accumulates_concurrent_execution_fees_without_lost_updates(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory)

    async with sqlite_session_factory() as session:
        trade = _build_entry_trade()
        trade.fee_paid = Decimal("0")
        session.add(trade)
        await session.commit()

    first = asyncio.create_task(
        engine._process_execution({"orderLinkId": "entry-1", "execFee": "0.10"})
    )
    second = asyncio.create_task(
        engine._process_execution({"orderLinkId": "entry-1", "execFee": "0.20"})
    )

    await asyncio.gather(first, second)

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()

    assert persisted_trade.fee_paid == Decimal("0.30")


@pytest.mark.asyncio
async def test_exchange_sync_spot_market_fill_arms_stop_loss_and_take_profit(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    rest_client = ProtectionRestClient()
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory, rest_client=rest_client)

    async with sqlite_session_factory() as session:
        trade = _build_entry_trade(order_type="Market")
        session.add(trade)
        await session.commit()

    await engine._process_order(  # noqa: SLF001
        {
            "orderLinkId": "entry-1",
            "orderStatus": "Filled",
            "avgPrice": "101",
            "cumExecQty": "1",
            "leavesQty": "0",
        }
    )

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()

    assert persisted_trade.status == TradeStatus.POSITION_OPEN
    assert persisted_trade.avg_fill_price == Decimal("101")
    assert persisted_trade.stop_order_link_id is not None
    assert persisted_trade.stop_exchange_order_id == "exchange-1"
    assert persisted_trade.take_profit_order_link_id is not None
    assert persisted_trade.take_profit_exchange_order_id == "exchange-2"
    assert len(rest_client.place_order_calls) == 2
    assert rest_client.place_order_calls[0]["trigger_price"] == "90.00000000"
    assert rest_client.place_order_calls[1]["trigger_price"] == "130.00000000"


@pytest.mark.asyncio
async def test_exchange_sync_emergency_closes_when_stop_loss_cannot_be_armed(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    rest_client = StopLossFailureRestClient()
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory, rest_client=rest_client)

    async with sqlite_session_factory() as session:
        trade = _build_entry_trade(order_type="Market")
        session.add(trade)
        await session.commit()

    await engine._process_order(  # noqa: SLF001
        {
            "orderLinkId": "entry-1",
            "orderStatus": "Filled",
            "avgPrice": "101",
            "cumExecQty": "1",
            "leavesQty": "0",
        }
    )

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()
        system_events = (await session.execute(select(SystemEvent))).scalars().all()
        safety_state = (await session.execute(select(SafetyState))).scalar_one()

    assert persisted_trade.status == TradeStatus.POSITION_CLOSE_PENDING
    assert persisted_trade.exit_reason == ExitReason.KILL_SWITCH
    assert persisted_trade.close_order_link_id is not None
    assert persisted_trade.close_exchange_order_id == "exchange-2"
    assert safety_state.kill_switch_active is True
    assert any("Stop-loss protective order could not be armed" in (event.description or "") for event in system_events)


@pytest.mark.asyncio
async def test_exchange_sync_retries_missing_take_profit_during_reconciliation(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    rest_client = ProtectionRestClient()
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory, rest_client=rest_client)

    async with sqlite_session_factory() as session:
        trade = _build_entry_trade(status=TradeStatus.POSITION_OPEN, order_type="Market")
        trade.stop_order_link_id = "stop-existing-1"
        trade.stop_exchange_order_id = "exchange-stop-1"
        trade.take_profit_order_link_id = None
        session.add(trade)
        await session.commit()

    reconciled = await engine.reconcile_missing_protection_orders()

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()

    assert reconciled == 1
    assert persisted_trade.take_profit_order_link_id is not None
    assert persisted_trade.take_profit_exchange_order_id == "exchange-1"
    assert len(rest_client.place_order_calls) == 1
