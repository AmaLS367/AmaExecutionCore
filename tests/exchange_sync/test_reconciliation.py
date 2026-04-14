from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.exchange_sync.engine import ExchangeSyncEngine
from backend.position_manager.service import PositionManagerService
from backend.trade_journal.models import (
    ExchangeSide,
    MarketType,
    SignalDirection,
    SystemEvent,
    Trade,
    TradeStatus,
    TradingMode,
)


class RecordingRestClient:
    def __init__(
        self,
        *,
        responses: dict[str, list[dict[str, object] | None]] | None = None,
    ) -> None:
        self._responses = {key: list(value) for key, value in (responses or {}).items()}
        self.status_calls: list[dict[str, str]] = []
        self.place_order_calls: list[dict[str, object]] = []

    def get_order_status(
        self,
        *,
        category: str,
        symbol: str,
        order_id: str | None = None,
        order_link_id: str | None = None,
    ) -> dict[str, object] | None:
        assert order_link_id is not None
        self.status_calls.append(
            {
                "category": category,
                "symbol": symbol,
                "order_link_id": order_link_id,
            }
        )
        queued = self._responses.get(order_link_id, [])
        if queued:
            return queued.pop(0)
        return None

    def place_order(self, **kwargs: object) -> dict[str, object]:
        self.place_order_calls.append(dict(kwargs))
        return {"orderId": f"close-{len(self.place_order_calls)}"}

    def queue_response(self, *, order_link_id: str, response: dict[str, object] | None) -> None:
        self._responses.setdefault(order_link_id, []).append(response)


def build_trade(
    *,
    status: TradeStatus,
    order_link_id: str,
    close_order_link_id: str | None = None,
) -> Trade:
    return Trade(
        signal_id=uuid.uuid4(),
        order_link_id=order_link_id,
        close_order_link_id=close_order_link_id,
        symbol="BTCUSDT",
        signal_direction=SignalDirection.LONG,
        exchange_side=ExchangeSide.BUY,
        market_type=MarketType.SPOT,
        mode=TradingMode.DEMO,
        entry_price=Decimal("100"),
        avg_fill_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("130"),
        qty=Decimal("1"),
        filled_qty=Decimal("1"),
        equity_at_entry=Decimal("1000"),
        risk_amount_usd=Decimal("10"),
        risk_pct=Decimal("0.01"),
        status=status,
        opened_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_reconcile_restores_order_submitted_when_ws_fill_is_missed(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    rest_client = RecordingRestClient(
        responses={
            "entry-1": [
                {
                    "orderId": "exchange-entry-1",
                    "orderStatus": "Filled",
                    "avgPrice": "101",
                    "cumExecQty": "1",
                    "leavesQty": "0",
                }
            ]
        }
    )
    engine = ExchangeSyncEngine(
        session_factory=sqlite_session_factory,
        rest_client=rest_client,
    )

    async with sqlite_session_factory() as session:
        session.add(
            build_trade(
                status=TradeStatus.ORDER_SUBMITTED,
                order_link_id="entry-1",
            )
        )
        await session.commit()

    await engine.reconcile_once()

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()
        trade_count = await session.scalar(select(func.count(Trade.id)))

    assert trade_count == 1
    assert persisted_trade.status == TradeStatus.POSITION_OPEN
    assert persisted_trade.exchange_order_id == "exchange-entry-1"
    assert persisted_trade.avg_fill_price == Decimal("101")
    assert persisted_trade.filled_qty == Decimal("1")
    assert persisted_trade.opened_at is not None
    assert rest_client.place_order_calls == []


@pytest.mark.asyncio
async def test_reconcile_pending_unknown_survives_restart_until_rest_state_is_known(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    initial_client = RecordingRestClient(responses={"entry-unknown": [None]})
    first_engine = ExchangeSyncEngine(
        session_factory=sqlite_session_factory,
        rest_client=initial_client,
    )

    async with sqlite_session_factory() as session:
        session.add(
            build_trade(
                status=TradeStatus.ORDER_PENDING_UNKNOWN,
                order_link_id="entry-unknown",
            )
        )
        await session.commit()

    await first_engine.reconcile_once()

    async with sqlite_session_factory() as session:
        pending_trade = (await session.execute(select(Trade))).scalar_one()
        assert pending_trade.status == TradeStatus.ORDER_PENDING_UNKNOWN
        assert await session.scalar(select(func.count(Trade.id))) == 1

    restarted_client = RecordingRestClient(
        responses={
            "entry-unknown": [
                {
                    "orderId": "exchange-entry-unknown",
                    "orderStatus": "Filled",
                    "avgPrice": "100",
                    "cumExecQty": "1",
                    "leavesQty": "0",
                }
            ]
        }
    )
    restarted_engine = ExchangeSyncEngine(
        session_factory=sqlite_session_factory,
        rest_client=restarted_client,
    )

    await restarted_engine.reconcile_once()

    async with sqlite_session_factory() as session:
        recovered_trade = (await session.execute(select(Trade))).scalar_one()

    assert recovered_trade.status == TradeStatus.POSITION_OPEN
    assert recovered_trade.exchange_order_id == "exchange-entry-unknown"
    assert initial_client.place_order_calls == []
    assert restarted_client.place_order_calls == []


@pytest.mark.asyncio
async def test_reconcile_restores_pending_close_when_ws_close_fill_is_missed(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    rest_client = RecordingRestClient(
        responses={
            "close-1": [
                {
                    "orderId": "exchange-close-1",
                    "orderStatus": "Filled",
                    "avgPrice": "110",
                    "cumExecQty": "1",
                    "leavesQty": "0",
                }
            ]
        }
    )
    engine = ExchangeSyncEngine(
        session_factory=sqlite_session_factory,
        rest_client=rest_client,
    )

    async with sqlite_session_factory() as session:
        session.add(
            build_trade(
                status=TradeStatus.POSITION_CLOSE_PENDING,
                order_link_id="entry-2",
                close_order_link_id="close-1",
            )
        )
        await session.commit()

    await engine.reconcile_once()

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()

    assert persisted_trade.status == TradeStatus.PNL_RECORDED
    assert persisted_trade.close_exchange_order_id == "exchange-close-1"
    assert persisted_trade.avg_exit_price == Decimal("110")
    assert persisted_trade.realized_pnl == Decimal("10")
    assert rest_client.place_order_calls == []


@pytest.mark.asyncio
async def test_close_failure_recovery_resubmits_once_and_reconciles_same_trade(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "demo"
    rest_client = RecordingRestClient()
    engine = ExchangeSyncEngine(
        session_factory=sqlite_session_factory,
        rest_client=rest_client,
    )
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=rest_client,
    )

    async with sqlite_session_factory() as session:
        trade = build_trade(
            status=TradeStatus.POSITION_OPEN,
            order_link_id="entry-3",
        )
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    first_close = await service.close_trade(trade_id=trade_id)
    first_close_order_link_id = first_close.close_order_link_id
    assert first_close_order_link_id is not None

    await engine._process_order(  # noqa: SLF001
        {
            "orderLinkId": first_close_order_link_id,
            "orderStatus": "Rejected",
        }
    )

    retried_close = await service.close_trade(trade_id=trade_id)
    retried_close_order_link_id = retried_close.close_order_link_id

    assert retried_close_order_link_id is not None
    assert retried_close_order_link_id != first_close_order_link_id

    rest_client.queue_response(
        order_link_id=retried_close_order_link_id,
        response={
            "orderId": "exchange-close-2",
            "orderStatus": "Filled",
            "avgPrice": "115",
            "cumExecQty": "1",
            "leavesQty": "0",
        },
    )

    await engine.reconcile_once()

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade))).scalar_one()
        trade_count = await session.scalar(select(func.count(Trade.id)))
        event_count = await session.scalar(select(func.count(SystemEvent.id)))

    assert trade_count == 1
    assert event_count == 1
    assert persisted_trade.id == trade_id
    assert persisted_trade.status == TradeStatus.PNL_RECORDED
    assert persisted_trade.close_order_link_id == retried_close_order_link_id
    assert len(rest_client.place_order_calls) == 2
