from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.trade_journal.models import (
    DailyStat,
    ExchangeSide,
    MarketType,
    PauseReason,
    SafetyState,
    SignalDirection,
    Trade,
    TradeEvent,
    TradeStatus,
    TradingMode,
)
from backend.trade_journal.store import TradeJournalStore


@pytest.mark.asyncio
async def test_postgresql_schema_migrated_via_alembic_supports_core_trade_journal_flows(
    postgresql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with postgresql_session_factory() as session:
        store = TradeJournalStore(session)
        signal = await store.create_signal(
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            reason="postgres-alembic-test",
            strategy_version="test-v1",
            indicators_snapshot={"ema_fast": 9, "ema_slow": 21},
        )

        trade = Trade(
            signal_id=signal.id,
            order_link_id="entry-postgres-1",
            symbol="BTCUSDT",
            signal_direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            market_type=MarketType.SPOT,
            mode=TradingMode.SHADOW,
            equity_at_entry=Decimal("1000"),
            risk_amount_usd=Decimal("10"),
            risk_pct=Decimal("0.01"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("130"),
            expected_rrr=Decimal("3"),
            qty=Decimal("1"),
            status=TradeStatus.RISK_CALCULATED,
            opened_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        session.add(trade)
        await session.flush()
        await store.record_trade_created(trade, event_metadata={"source": "postgres-test"})
        await store.transition_trade_status(
            trade,
            TradeStatus.ORDER_PENDING_UNKNOWN,
            event_metadata={"source": "postgres-test"},
        )
        await store.set_pause(
            pause_reason=PauseReason.HARD_LOSS_STREAK,
            manual_reset_required=True,
        )
        trade.fee_paid = Decimal("1.5")
        trade.realized_pnl = Decimal("15")
        trade.pnl_pct = Decimal("0.015")
        trade.closed_at = datetime(2024, 1, 1, 1, tzinfo=UTC)
        await store.apply_trade_outcome_analytics(trade)
        await session.commit()

    async with postgresql_session_factory() as verification_session:
        alembic_revision = await verification_session.scalar(text("SELECT version_num FROM alembic_version"))
        persisted_trade = (
            await verification_session.execute(select(Trade).where(Trade.order_link_id == "entry-postgres-1"))
        ).scalar_one()
        persisted_state = (await verification_session.execute(select(SafetyState))).scalar_one()
        daily_stat = (await verification_session.execute(select(DailyStat))).scalar_one()
        trade_events = (
            await verification_session.execute(
                select(TradeEvent).where(TradeEvent.trade_id == persisted_trade.id).order_by(TradeEvent.id)
            )
        ).scalars().all()

    assert alembic_revision is not None
    assert persisted_trade.status == TradeStatus.ORDER_PENDING_UNKNOWN
    assert persisted_trade.risk_amount_usd == Decimal("10.00000000")
    assert persisted_state is not None
    assert persisted_state.pause_reason == PauseReason.HARD_LOSS_STREAK
    assert persisted_state.manual_reset_required is True
    assert daily_stat.net_pnl == Decimal("13.50000000")
    assert [(event.event_type, event.from_status, event.to_status) for event in trade_events] == [
        ("trade_created", None, TradeStatus.RISK_CALCULATED.value),
        (
            "status_transition",
            TradeStatus.RISK_CALCULATED.value,
            TradeStatus.ORDER_PENDING_UNKNOWN.value,
        ),
    ]


@pytest.mark.asyncio
async def test_postgresql_schema_migrated_via_alembic_enforces_unique_trade_order_link_id(
    postgresql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with postgresql_session_factory() as session:
        first_trade = Trade(
            signal_id=None,
            order_link_id="duplicate-order-link",
            symbol="BTCUSDT",
            signal_direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            market_type=MarketType.SPOT,
            mode=TradingMode.SHADOW,
            status=TradeStatus.RISK_CALCULATED,
        )
        second_trade = Trade(
            signal_id=None,
            order_link_id="duplicate-order-link",
            symbol="ETHUSDT",
            signal_direction=SignalDirection.SHORT,
            exchange_side=ExchangeSide.SELL,
            market_type=MarketType.SPOT,
            mode=TradingMode.SHADOW,
            status=TradeStatus.RISK_CALCULATED,
        )
        session.add(first_trade)
        await session.flush()
        session.add(second_trade)

        with pytest.raises(IntegrityError):
            await session.flush()
