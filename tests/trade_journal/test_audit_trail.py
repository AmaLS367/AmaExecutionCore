from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from textwrap import dedent
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.exchange_sync.engine import ExchangeSyncEngine
from backend.order_executor.executor import OrderExecutor
from backend.position_manager.service import PositionManagerService
from backend.signal_execution.schemas import ExecuteSignalRequest
from backend.signal_execution.service import ExecutionService
from backend.trade_journal.models import (
    DailyStat,
    ExchangeSide,
    ExitReason,
    MarketType,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)


class ShadowRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}


class PendingUnknownReplayRestClient:
    def __init__(self) -> None:
        self.place_order_calls = 0
        self.get_order_status_calls = 0

    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}

    def get_instruments_info(self, symbol: str, category: str = "spot") -> dict[str, object]:
        return {"lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1", "minOrderAmt": "5"}}

    def place_order(self, **_: object) -> dict[str, object]:
        self.place_order_calls += 1
        raise TimeoutError("simulated timeout")

    def get_order_status(self, **_: object) -> dict[str, object] | None:
        self.get_order_status_calls += 1
        if self.get_order_status_calls == 1:
            return None
        return {"orderId": "resolved-1", "orderStatus": "Filled"}


class PassiveRestClient:
    def place_order(self, **_: object) -> dict[str, object]:
        return {"orderId": "unused"}


async def assert_trade_events_table_exists(session: AsyncSession) -> None:
    table_name = await session.scalar(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='trade_events'")
    )
    assert table_name == "trade_events"


async def fetch_trade_events(
    session: AsyncSession,
    *,
    trade_id: UUID,
) -> list[dict[str, object]]:
    await assert_trade_events_table_exists(session)
    result = await session.execute(
        text(
            dedent(
                """
                SELECT event_type, from_status, to_status, metadata
                FROM trade_events
                WHERE lower(replace(trade_id, '-', '')) = :trade_id
                ORDER BY id
                """
            )
        ),
        {"trade_id": trade_id.hex},
    )
    events: list[dict[str, object]] = []
    for row in result.mappings().all():
        raw_metadata = row["metadata"]
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        else:
            metadata = raw_metadata
        events.append(
            {
                "event_type": row["event_type"],
                "from_status": row["from_status"],
                "to_status": row["to_status"],
                "metadata": metadata,
            }
        )
    return events


def build_open_trade() -> Trade:
    return Trade(
        signal_id=uuid4(),
        order_link_id=f"entry-{uuid4().hex[:8]}",
        symbol="BTCUSDT",
        signal_direction=SignalDirection.LONG,
        exchange_side=ExchangeSide.BUY,
        market_type=MarketType.SPOT,
        mode=TradingMode.SHADOW,
        entry_price=Decimal("100"),
        avg_fill_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("130"),
        qty=Decimal("1"),
        filled_qty=Decimal("1"),
        equity_at_entry=Decimal("1000"),
        risk_amount_usd=Decimal("10"),
        status=TradeStatus.POSITION_OPEN,
    )


def test_shadow_execution_persists_trade_creation_and_status_events(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    service = ExecutionService(
        session_factory=sqlite_session_factory,
        order_executor=OrderExecutor(rest_client=ShadowRestClient()),
    )

    async def verify() -> None:
        result = await service.execute_signal(
            signal=ExecuteSignalRequest(
                symbol="BTCUSDT",
                direction="long",
                entry=100.0,
                stop=90.0,
                target=130.0,
            )
        )
        async with sqlite_session_factory() as session:
            trade_id = UUID(str(result.trade_id))
            events = await fetch_trade_events(session, trade_id=trade_id)
            assert [(event["event_type"], event["from_status"], event["to_status"]) for event in events] == [
                ("trade_created", None, TradeStatus.RISK_CALCULATED.value),
                (
                    "status_transition",
                    TradeStatus.RISK_CALCULATED.value,
                    TradeStatus.ORDER_SUBMITTED.value,
                ),
            ]

    asyncio.run(verify())


def test_pending_unknown_reconciliation_appends_transition_history(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "demo"
    rest_client = PendingUnknownReplayRestClient()
    service = ExecutionService(
        session_factory=sqlite_session_factory,
        order_executor=OrderExecutor(rest_client=rest_client),
    )

    async def verify() -> None:
        first_result = await service.execute_signal(
            signal=ExecuteSignalRequest(
                symbol="BTCUSDT",
                direction="long",
                entry=100.0,
                stop=90.0,
                target=130.0,
            )
        )
        second_result = await service.execute_signal(
            signal=ExecuteSignalRequest(
                symbol="BTCUSDT",
                direction="long",
                entry=100.0,
                stop=90.0,
                target=130.0,
            )
        )
        assert second_result.trade_id == first_result.trade_id

        async with sqlite_session_factory() as session:
            trade_id = UUID(str(first_result.trade_id))
            events = await fetch_trade_events(session, trade_id=trade_id)
            assert [(event["from_status"], event["to_status"]) for event in events] == [
                (None, TradeStatus.RISK_CALCULATED.value),
                (TradeStatus.RISK_CALCULATED.value, TradeStatus.SAFETY_CHECKED.value),
                (TradeStatus.SAFETY_CHECKED.value, TradeStatus.ORDER_PENDING_UNKNOWN.value),
                (
                    TradeStatus.ORDER_PENDING_UNKNOWN.value,
                    TradeStatus.ORDER_CONFIRMED.value,
                ),
            ]

    asyncio.run(verify())


@pytest.mark.asyncio
async def test_exchange_sync_close_updates_daily_analytics_and_records_events(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = ExchangeSyncEngine(session_factory=sqlite_session_factory)

    async with sqlite_session_factory() as session:
        trade = Trade(
            signal_id=uuid4(),
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
            fee_paid=Decimal("1.5"),
            status=TradeStatus.POSITION_CLOSE_PENDING,
        )
        session.add(trade)
        await session.commit()
        trade_id = trade.id

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
        persisted_trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()
        daily_stat = (await session.execute(select(DailyStat))).scalar_one()
        events = await fetch_trade_events(session, trade_id=trade_id)

    assert persisted_trade.status == TradeStatus.PNL_RECORDED
    assert daily_stat.total_trades == 1
    assert daily_stat.winning_trades == 1
    assert daily_stat.gross_pnl == Decimal("10")
    assert daily_stat.total_fees == Decimal("1.5")
    assert daily_stat.net_pnl == Decimal("8.5")
    assert [(event["from_status"], event["to_status"]) for event in events] == [
        (TradeStatus.POSITION_CLOSE_PENDING.value, TradeStatus.ORDER_CONFIRMED.value),
        (TradeStatus.ORDER_CONFIRMED.value, TradeStatus.POSITION_CLOSED.value),
        (TradeStatus.POSITION_CLOSED.value, TradeStatus.PNL_RECORDED.value),
    ]


@pytest.mark.asyncio
async def test_shadow_close_updates_daily_analytics_and_records_events(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=PassiveRestClient(),
    )

    async with sqlite_session_factory() as session:
        trade = build_open_trade()
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    await service.close_trade(trade_id=trade_id, exit_reason=ExitReason.SL_HIT)

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()
        daily_stat = (await session.execute(select(DailyStat))).scalar_one()
        events = await fetch_trade_events(session, trade_id=trade_id)

    assert persisted_trade.status == TradeStatus.PNL_RECORDED
    assert daily_stat.total_trades == 1
    assert daily_stat.losing_trades == 1
    assert daily_stat.consecutive_losses == 1
    assert daily_stat.gross_pnl == Decimal("-10")
    assert daily_stat.total_fees == Decimal("0")
    assert daily_stat.net_pnl == Decimal("-10")
    assert daily_stat.daily_loss_pct == Decimal("0.01")
    assert [(event["from_status"], event["to_status"]) for event in events] == [
        (TradeStatus.POSITION_OPEN.value, TradeStatus.POSITION_CLOSE_PENDING.value),
        (
            TradeStatus.POSITION_CLOSE_PENDING.value,
            TradeStatus.PNL_RECORDED.value,
        ),
    ]
