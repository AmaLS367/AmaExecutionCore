from __future__ import annotations

from decimal import Decimal
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.order_executor.executor import OrderExecutor
from backend.risk_manager.exceptions import RiskManagerError
from backend.trade_journal.models import (
    ExchangeSide,
    MarketType,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)


class TimeoutRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        return {"list": [{"coin": [{"coin": "USDT", "equity": "1000"}]}]}

    def get_instruments_info(self, symbol: str, category: str = "spot") -> dict[str, object]:
        return {"lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1", "minOrderAmt": "5"}}

    def place_order(self, **_: object) -> dict[str, object]:
        raise TimeoutError("simulated timeout")

    def get_order_status(self, **_: object) -> dict[str, object] | None:
        return None


class ResolvePendingUnknownRestClient:
    def get_order_status(self, **_: object) -> dict[str, object] | None:
        return {"orderId": "resolved-2", "orderStatus": "Filled"}


@pytest.mark.asyncio
async def test_executor_marks_pending_unknown_after_submit_timeout(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "demo"
    executor = OrderExecutor(rest_client=TimeoutRestClient())

    async with sqlite_session_factory() as session:
        trade = await executor.execute(
            session=session,
            signal_id=uuid.uuid4(),
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            entry=100.0,
            stop=90.0,
            target=130.0,
        )

        persisted_trade = (
            await session.execute(select(Trade).where(Trade.id == trade.id))
        ).scalar_one()
        assert persisted_trade.status == TradeStatus.ORDER_PENDING_UNKNOWN


@pytest.mark.asyncio
async def test_executor_blocks_when_max_positions_reached(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    settings.max_open_positions = 1
    executor = OrderExecutor(rest_client=TimeoutRestClient())

    async with sqlite_session_factory() as session:
        session.add(
            Trade(
                signal_id=uuid.uuid4(),
                order_link_id="open-1",
                symbol="BTCUSDT",
                signal_direction=SignalDirection.LONG,
                exchange_side=ExchangeSide.BUY,
                market_type=MarketType.SPOT,
                mode=TradingMode.SHADOW,
                risk_amount_usd=Decimal("100"),
                status=TradeStatus.POSITION_OPEN,
            )
        )
        await session.commit()

        with pytest.raises(RiskManagerError, match="Max open positions"):
            await executor.execute(
                session=session,
                signal_id=uuid.uuid4(),
                symbol="ETHUSDT",
                direction=SignalDirection.LONG,
                entry=100.0,
                stop=90.0,
                target=130.0,
            )


@pytest.mark.asyncio
async def test_executor_blocks_when_exposure_limit_reached(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    settings.max_open_positions = 2
    settings.max_total_risk_exposure_pct = 0.03
    executor = OrderExecutor(rest_client=TimeoutRestClient())

    async with sqlite_session_factory() as session:
        session.add(
            Trade(
                signal_id=uuid.uuid4(),
                order_link_id="open-2",
                symbol="BTCUSDT",
                signal_direction=SignalDirection.LONG,
                exchange_side=ExchangeSide.BUY,
                market_type=MarketType.SPOT,
                mode=TradingMode.SHADOW,
                risk_amount_usd=Decimal("300"),
                qty=Decimal("1"),
                filled_qty=Decimal("1"),
                status=TradeStatus.POSITION_OPEN,
            )
        )
        await session.commit()

        with pytest.raises(RiskManagerError, match="Total risk exposure limit"):
            await executor.execute(
                session=session,
                signal_id=uuid.uuid4(),
                symbol="ETHUSDT",
                direction=SignalDirection.LONG,
                entry=100.0,
                stop=90.0,
                target=130.0,
            )


@pytest.mark.asyncio
async def test_executor_uses_configured_shadow_equity(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    settings.shadow_equity = 5_000.0
    executor = OrderExecutor(rest_client=TimeoutRestClient())

    async with sqlite_session_factory() as session:
        trade = await executor.execute(
            session=session,
            signal_id=uuid.uuid4(),
            symbol="BTCUSDT",
            direction=SignalDirection.LONG,
            entry=100.0,
            stop=90.0,
            target=130.0,
        )

        persisted_trade = (
            await session.execute(select(Trade).where(Trade.id == trade.id))
        ).scalar_one()
        assert persisted_trade.equity_at_entry == Decimal("5000")


@pytest.mark.asyncio
async def test_executor_reconciles_pending_unknown_without_changing_order_link_id(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    executor = OrderExecutor(rest_client=ResolvePendingUnknownRestClient())
    signal_id = uuid.uuid4()

    async with sqlite_session_factory() as session:
        trade = Trade(
            signal_id=signal_id,
            order_link_id="existing-link-1",
            symbol="BTCUSDT",
            signal_direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            market_type=MarketType.SPOT,
            mode=TradingMode.DEMO,
            status=TradeStatus.ORDER_PENDING_UNKNOWN,
        )
        session.add(trade)
        await session.commit()

        await executor.reconcile_pending_unknown(session=session, trade=trade)
        await session.flush()

        persisted_trade = (
            await session.execute(select(Trade).where(Trade.id == trade.id))
        ).scalar_one()
        assert persisted_trade.order_link_id == "existing-link-1"
        assert persisted_trade.exchange_order_id == "resolved-2"
        assert persisted_trade.status == TradeStatus.ORDER_CONFIRMED
