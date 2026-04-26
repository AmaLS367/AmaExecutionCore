from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.backtest.demo_runner import DemoRunner
from backend.config import settings
from backend.trade_journal.models import (
    ExchangeSide,
    MarketType,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)


def _build_trade(*, trade_id: uuid.UUID, status: TradeStatus) -> Trade:
    return Trade(
        id=trade_id,
        signal_id=uuid.uuid4(),
        order_link_id=f"order-{trade_id.hex[:8]}",
        symbol="BTCUSDT",
        signal_direction=SignalDirection.LONG,
        exchange_side=ExchangeSide.BUY,
        market_type=MarketType.SPOT,
        mode=TradingMode.DEMO,
        entry_price=Decimal(100),
        stop_price=Decimal(90),
        target_price=Decimal(130),
        qty=Decimal(1),
        filled_qty=Decimal(1),
        status=status,
        opened_at=datetime.now(UTC),
    )


@dataclass(slots=True)
class _ExecutionResult:
    trade_id: uuid.UUID


class _FakeExecutionService:
    def __init__(self, trade_id: uuid.UUID) -> None:
        self.trade_id = trade_id
        self.signals: list[object] = []

    async def execute_signal(self, *, signal: object) -> _ExecutionResult:
        self.signals.append(signal)
        return _ExecutionResult(trade_id=self.trade_id)


class _ClosingPositionManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        target_status: TradeStatus,
    ) -> None:
        self._session_factory = session_factory
        self._target_status = target_status
        self.closed_trade_ids: list[uuid.UUID] = []

    async def close_trade(self, *, trade_id: uuid.UUID) -> None:
        self.closed_trade_ids.append(trade_id)
        async with self._session_factory() as session:
            trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()
            trade.status = self._target_status
            await session.commit()


class _PromotingSyncEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        trade_id: uuid.UUID,
        promote_on_call: int,
        target_status: TradeStatus,
    ) -> None:
        self._session_factory = session_factory
        self._trade_id = trade_id
        self._promote_on_call = promote_on_call
        self._target_status = target_status
        self.calls = 0

    async def reconcile_once(self) -> None:
        self.calls += 1
        if self.calls != self._promote_on_call:
            return
        async with self._session_factory() as session:
            trade = (await session.execute(select(Trade).where(Trade.id == self._trade_id))).scalar_one()
            trade.status = self._target_status
            await session.commit()


@pytest.mark.asyncio
async def test_wait_for_trade_reconciles_until_target_status(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trade_id = uuid.uuid4()
    async with sqlite_session_factory() as session:
        session.add(_build_trade(trade_id=trade_id, status=TradeStatus.ORDER_SUBMITTED))
        await session.commit()

    settings.demo_poll_interval_seconds = 0.01
    sync_engine = _PromotingSyncEngine(
        sqlite_session_factory,
        trade_id,
        promote_on_call=2,
        target_status=TradeStatus.POSITION_OPEN,
    )
    runner = DemoRunner(
        execution_service=_FakeExecutionService(trade_id),
        position_manager=_ClosingPositionManager(sqlite_session_factory, TradeStatus.PNL_RECORDED),
        session_factory=sqlite_session_factory,
        sync_engine=sync_engine,
    )

    trade = await runner._wait_for_trade(
        trade_id=trade_id,
        accepted_statuses={TradeStatus.POSITION_OPEN},
        timeout_seconds=0.2,
    )

    assert trade.status == TradeStatus.POSITION_OPEN
    assert sync_engine.calls >= 2


@pytest.mark.asyncio
async def test_wait_for_trade_raises_timeout_when_status_never_arrives(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trade_id = uuid.uuid4()
    async with sqlite_session_factory() as session:
        session.add(_build_trade(trade_id=trade_id, status=TradeStatus.ORDER_SUBMITTED))
        await session.commit()

    settings.demo_poll_interval_seconds = 0.01
    runner = DemoRunner(
        execution_service=_FakeExecutionService(trade_id),
        position_manager=_ClosingPositionManager(sqlite_session_factory, TradeStatus.PNL_RECORDED),
        session_factory=sqlite_session_factory,
    )

    with pytest.raises(TimeoutError, match=str(trade_id)):
        await runner._wait_for_trade(
            trade_id=trade_id,
            accepted_statuses={TradeStatus.POSITION_OPEN},
            timeout_seconds=0.05,
        )


@pytest.mark.asyncio
async def test_execute_and_close_waits_for_open_then_closes_trade(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trade_id = uuid.uuid4()
    async with sqlite_session_factory() as session:
        session.add(_build_trade(trade_id=trade_id, status=TradeStatus.ORDER_SUBMITTED))
        await session.commit()

    settings.demo_poll_interval_seconds = 0.01
    settings.demo_close_ttl_seconds = 0.1
    execution_service = _FakeExecutionService(trade_id)
    position_manager = _ClosingPositionManager(sqlite_session_factory, TradeStatus.PNL_RECORDED)
    sync_engine = _PromotingSyncEngine(
        sqlite_session_factory,
        trade_id,
        promote_on_call=1,
        target_status=TradeStatus.POSITION_OPEN,
    )
    runner = DemoRunner(
        execution_service=execution_service,
        position_manager=position_manager,
        session_factory=sqlite_session_factory,
        sync_engine=sync_engine,
    )

    trade = await runner.execute_and_close(signal={"symbol": "BTCUSDT"})

    assert trade.id == trade_id
    assert trade.status == TradeStatus.PNL_RECORDED
    assert execution_service.signals == [{"symbol": "BTCUSDT"}]
    assert position_manager.closed_trade_ids == [trade_id]
