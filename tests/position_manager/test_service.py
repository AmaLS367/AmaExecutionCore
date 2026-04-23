from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.position_manager.service import PositionManagerService
from backend.trade_journal.models import (
    ExchangeSide,
    ExitReason,
    MarketType,
    SignalDirection,
    Trade,
    TradeStatus,
    TradingMode,
)


class PassiveRestClient:
    def place_order(self, **_: object) -> dict[str, object]:
        return {"orderId": "unused"}

    def get_ticker_price(self, symbol: str, category: str = "spot") -> float:
        del symbol, category
        return 100.0


class RecordingTickerRestClient(PassiveRestClient):
    def __init__(self, *, price_by_symbol: dict[str, float]) -> None:
        self.price_by_symbol = price_by_symbol
        self.price_calls: list[tuple[str, str]] = []

    def get_ticker_price(self, symbol: str, category: str = "spot") -> float:
        self.price_calls.append((symbol, category))
        return self.price_by_symbol[symbol]


def build_open_trade(
    *,
    direction: SignalDirection,
    exchange_side: ExchangeSide,
    entry_price: str,
    stop_price: str,
    target_price: str | None,
) -> Trade:
    return Trade(
        signal_id=uuid.uuid4(),
        order_link_id=f"entry-{uuid.uuid4().hex[:8]}",
        symbol="BTCUSDT",
        signal_direction=direction,
        exchange_side=exchange_side,
        market_type=MarketType.SPOT,
        mode=TradingMode.SHADOW,
        entry_price=Decimal(entry_price),
        avg_fill_price=Decimal(entry_price),
        stop_price=Decimal(stop_price),
        target_price=Decimal(target_price) if target_price is not None else None,
        qty=Decimal(1),
        filled_qty=Decimal(1),
        equity_at_entry=Decimal(1000),
        risk_amount_usd=Decimal(10),
        status=TradeStatus.POSITION_OPEN,
        opened_at=datetime.now(UTC) - timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_close_trade_in_shadow_records_positive_long_pnl(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=PassiveRestClient(),
    )

    async with sqlite_session_factory() as session:
        trade = build_open_trade(
            direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            entry_price="100",
            stop_price="90",
            target_price="130",
        )
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    closed_trade = await service.close_trade(trade_id=trade_id, exit_reason=ExitReason.TP_HIT)

    assert closed_trade.status == TradeStatus.PNL_RECORDED
    assert closed_trade.realized_pnl == Decimal(30)
    assert closed_trade.pnl_pct is not None
    assert closed_trade.pnl_pct > 0
    assert closed_trade.pnl_in_r is not None
    assert closed_trade.pnl_in_r > 0
    assert closed_trade.hold_time_seconds is not None
    assert closed_trade.hold_time_seconds >= 0


@pytest.mark.asyncio
async def test_close_trade_in_shadow_records_positive_short_pnl(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=PassiveRestClient(),
    )

    async with sqlite_session_factory() as session:
        trade = build_open_trade(
            direction=SignalDirection.SHORT,
            exchange_side=ExchangeSide.SELL,
            entry_price="100",
            stop_price="110",
            target_price="70",
        )
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    closed_trade = await service.close_trade(trade_id=trade_id, exit_reason=ExitReason.TP_HIT)

    assert closed_trade.realized_pnl == Decimal(30)


@pytest.mark.asyncio
async def test_close_trade_in_shadow_records_negative_sl_pnl(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=PassiveRestClient(),
    )

    async with sqlite_session_factory() as session:
        trade = build_open_trade(
            direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            entry_price="100",
            stop_price="90",
            target_price=None,
        )
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    closed_trade = await service.close_trade(trade_id=trade_id, exit_reason=ExitReason.SL_HIT)

    assert closed_trade.realized_pnl is not None
    assert closed_trade.realized_pnl < 0


@pytest.mark.asyncio
async def test_list_open_trades_includes_close_recovery_states(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=PassiveRestClient(),
    )

    async with sqlite_session_factory() as session:
        session.add_all(
            [
                build_open_trade(
                    direction=SignalDirection.LONG,
                    exchange_side=ExchangeSide.BUY,
                    entry_price="100",
                    stop_price="90",
                    target_price="130",
                ),
                build_open_trade(
                    direction=SignalDirection.LONG,
                    exchange_side=ExchangeSide.BUY,
                    entry_price="100",
                    stop_price="90",
                    target_price="130",
                ),
                build_open_trade(
                    direction=SignalDirection.LONG,
                    exchange_side=ExchangeSide.BUY,
                    entry_price="100",
                    stop_price="90",
                    target_price="130",
                ),
                build_open_trade(
                    direction=SignalDirection.LONG,
                    exchange_side=ExchangeSide.BUY,
                    entry_price="100",
                    stop_price="90",
                    target_price="130",
                ),
            ],
        )
        trades = (await session.execute(select(Trade))).scalars().all()
        trades[0].status = TradeStatus.POSITION_OPEN
        trades[1].status = TradeStatus.POSITION_CLOSE_PENDING
        trades[2].status = TradeStatus.POSITION_CLOSE_FAILED
        trades[3].status = TradeStatus.PNL_RECORDED
        await session.commit()

    open_trades = await service.list_open_trades()

    assert {trade.status for trade in open_trades} == {
        TradeStatus.POSITION_OPEN,
        TradeStatus.POSITION_CLOSE_PENDING,
        TradeStatus.POSITION_CLOSE_FAILED,
    }


@pytest.mark.asyncio
async def test_monitor_spot_exit_candidates_closes_long_trade_on_stop_loss(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    rest_client = RecordingTickerRestClient(price_by_symbol={"BTCUSDT": 89.0})
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=rest_client,
    )

    async with sqlite_session_factory() as session:
        trade = build_open_trade(
            direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            entry_price="100",
            stop_price="90",
            target_price="130",
        )
        trade.order_type = "Market"
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    closed = await service.monitor_spot_exit_candidates_once()

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()

    assert closed == 1
    assert persisted_trade.status == TradeStatus.PNL_RECORDED
    assert persisted_trade.exit_reason == ExitReason.SL_HIT
    assert rest_client.price_calls == [("BTCUSDT", "spot")]


@pytest.mark.asyncio
async def test_monitor_spot_exit_candidates_closes_long_trade_on_take_profit(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=RecordingTickerRestClient(price_by_symbol={"BTCUSDT": 131.0}),
    )

    async with sqlite_session_factory() as session:
        trade = build_open_trade(
            direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            entry_price="100",
            stop_price="90",
            target_price="130",
        )
        trade.order_type = "Market"
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    closed = await service.monitor_spot_exit_candidates_once()

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()

    assert closed == 1
    assert persisted_trade.exit_reason == ExitReason.TP_HIT


@pytest.mark.asyncio
async def test_monitor_spot_exit_candidates_closes_short_trade_on_stop_loss(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=RecordingTickerRestClient(price_by_symbol={"BTCUSDT": 111.0}),
    )

    async with sqlite_session_factory() as session:
        trade = build_open_trade(
            direction=SignalDirection.SHORT,
            exchange_side=ExchangeSide.SELL,
            entry_price="100",
            stop_price="110",
            target_price="70",
        )
        trade.order_type = "Market"
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    closed = await service.monitor_spot_exit_candidates_once()

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()

    assert closed == 1
    assert persisted_trade.exit_reason == ExitReason.SL_HIT


@pytest.mark.asyncio
async def test_monitor_spot_exit_candidates_closes_short_trade_on_take_profit(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=RecordingTickerRestClient(price_by_symbol={"BTCUSDT": 69.0}),
    )

    async with sqlite_session_factory() as session:
        trade = build_open_trade(
            direction=SignalDirection.SHORT,
            exchange_side=ExchangeSide.SELL,
            entry_price="100",
            stop_price="110",
            target_price="70",
        )
        trade.order_type = "Market"
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    closed = await service.monitor_spot_exit_candidates_once()

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()

    assert closed == 1
    assert persisted_trade.exit_reason == ExitReason.TP_HIT


@pytest.mark.asyncio
async def test_monitor_spot_exit_candidates_ignores_trades_without_trigger(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    rest_client = RecordingTickerRestClient(price_by_symbol={"BTCUSDT": 105.0})
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=rest_client,
    )

    async with sqlite_session_factory() as session:
        trade = build_open_trade(
            direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            entry_price="100",
            stop_price="90",
            target_price="130",
        )
        trade.order_type = "Market"
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    closed = await service.monitor_spot_exit_candidates_once()

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()

    assert closed == 0
    assert persisted_trade.status == TradeStatus.POSITION_OPEN
    assert rest_client.price_calls == [("BTCUSDT", "spot")]


@pytest.mark.asyncio
async def test_monitor_spot_exit_candidates_ignores_trades_with_existing_protection(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.trading_mode = "shadow"
    rest_client = RecordingTickerRestClient(price_by_symbol={"BTCUSDT": 89.0})
    service = PositionManagerService(
        session_factory=sqlite_session_factory,
        rest_client=rest_client,
    )

    async with sqlite_session_factory() as session:
        trade = build_open_trade(
            direction=SignalDirection.LONG,
            exchange_side=ExchangeSide.BUY,
            entry_price="100",
            stop_price="90",
            target_price="130",
        )
        trade.order_type = "Market"
        trade.stop_order_link_id = "stop-protection-1"
        session.add(trade)
        await session.commit()
        trade_id = trade.id

    closed = await service.monitor_spot_exit_candidates_once()

    async with sqlite_session_factory() as session:
        persisted_trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one()

    assert closed == 0
    assert persisted_trade.status == TradeStatus.POSITION_OPEN
    assert rest_client.price_calls == []
