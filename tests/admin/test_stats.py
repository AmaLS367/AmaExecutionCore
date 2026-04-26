from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.admin import stats as admin_stats
from backend.config import settings
from backend.trade_journal.models import (  # noqa: F401 — registers tables in Base
    DailyStat,
    ExchangeSide,
    MarketType,
    SafetyState,
    SignalDirection,
    Trade,
    TradingMode,
    TradeStatus,
)


@pytest.fixture(autouse=True)
def _configure() -> None:
    settings.admin_jwt_secret = "test-secret-at-least-32-characters-ok"
    settings.trading_mode = "shadow"
    settings.shadow_equity = 10_000.0


class _NullRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        raise RuntimeError("should not be called in shadow mode")


def _make_trade(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    status: TradeStatus = TradeStatus.POSITION_OPEN,
    realized_pnl: Decimal | None = None,
) -> None:
    trade = Trade(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        signal_direction=SignalDirection.LONG,
        exchange_side=ExchangeSide.BUY,
        market_type=MarketType.SPOT,
        mode=TradingMode.SHADOW,
        status=status,
        is_post_only=False,
        is_reduce_only=False,
        realized_pnl=realized_pnl,
        closed_at=datetime.now(UTC) if status == TradeStatus.POSITION_CLOSED else None,
    )

    async def _insert() -> None:
        async with session_factory() as session:
            session.add(trade)
            await session.commit()

    asyncio.run(_insert())


def _make_daily_stat(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    stat_date: date,
    ending_equity: Decimal | None = None,
    net_pnl: Decimal | None = None,
) -> None:
    async def _insert() -> None:
        async with session_factory() as session:
            session.add(
                DailyStat(
                    stat_date=stat_date,
                    ending_equity=ending_equity,
                    net_pnl=net_pnl,
                )
            )
            await session.commit()

    asyncio.run(_insert())


# ---------------------------------------------------------------------------
# get_dashboard_stats
# ---------------------------------------------------------------------------


def test_dashboard_shadow_mode_returns_shadow_equity(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> admin_stats.DashboardStats:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_dashboard_stats(
                session, rest_client=_NullRestClient()
            )

    result = asyncio.run(run())
    assert result.equity == 10_000.0
    assert result.trading_mode == "shadow"


def test_dashboard_returns_ok_status_when_safety_clear(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> admin_stats.DashboardStats:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_dashboard_stats(
                session, rest_client=_NullRestClient()
            )

    result = asyncio.run(run())
    assert result.safety_guard_status == "OK"
    assert result.open_positions_count == 0
    assert result.pnl_today == 0.0


def test_dashboard_counts_open_positions(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_trade(sqlite_session_factory, status=TradeStatus.POSITION_OPEN)

    async def run() -> admin_stats.DashboardStats:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_dashboard_stats(
                session, rest_client=_NullRestClient()
            )

    result = asyncio.run(run())
    assert result.open_positions_count == 1


def test_dashboard_returns_killed_when_kill_switch_active(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def _activate() -> None:
        async with sqlite_session_factory() as session:
            state = SafetyState(id=1, kill_switch_active=True)
            session.add(state)
            await session.commit()

    asyncio.run(_activate())

    async def run() -> admin_stats.DashboardStats:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_dashboard_stats(
                session, rest_client=_NullRestClient()
            )

    result = asyncio.run(run())
    assert result.safety_guard_status == "KILLED"


def test_dashboard_returns_paused_when_pause_reason_set(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from backend.trade_journal.models import PauseReason

    async def _pause() -> None:
        async with sqlite_session_factory() as session:
            state = SafetyState(
                id=1, kill_switch_active=False, pause_reason=PauseReason.DAILY_LOSS
            )
            session.add(state)
            await session.commit()

    asyncio.run(_pause())

    async def run() -> admin_stats.DashboardStats:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_dashboard_stats(
                session, rest_client=_NullRestClient()
            )

    result = asyncio.run(run())
    assert result.safety_guard_status == "PAUSED"


def test_dashboard_pnl_today_from_daily_stat(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_daily_stat(
        sqlite_session_factory, stat_date=datetime.now(UTC).date(), net_pnl=Decimal("250.50")
    )

    async def run() -> admin_stats.DashboardStats:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_dashboard_stats(
                session, rest_client=_NullRestClient()
            )

    result = asyncio.run(run())
    assert result.pnl_today == pytest.approx(250.50)


# ---------------------------------------------------------------------------
# get_equity_curve
# ---------------------------------------------------------------------------


def test_equity_curve_returns_points_for_requested_days(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    today = datetime.now(UTC).date()
    _make_daily_stat(
        sqlite_session_factory,
        stat_date=today - timedelta(days=1),
        ending_equity=Decimal("9500.00"),
    )
    _make_daily_stat(
        sqlite_session_factory, stat_date=today, ending_equity=Decimal("10000.00")
    )

    async def run() -> list[admin_stats.EquityPoint]:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_equity_curve(session, days=7)

    points = asyncio.run(run())
    assert len(points) == 2
    assert points[-1].equity == pytest.approx(10_000.0)


def test_equity_curve_excludes_old_entries(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    today = datetime.now(UTC).date()
    _make_daily_stat(
        sqlite_session_factory,
        stat_date=today - timedelta(days=60),
        ending_equity=Decimal("8000.00"),
    )
    _make_daily_stat(
        sqlite_session_factory, stat_date=today, ending_equity=Decimal("10000.00")
    )

    async def run() -> list[admin_stats.EquityPoint]:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_equity_curve(session, days=30)

    points = asyncio.run(run())
    assert len(points) == 1
    assert points[0].equity == pytest.approx(10_000.0)


# ---------------------------------------------------------------------------
# get_daily_pnl
# ---------------------------------------------------------------------------


def test_daily_pnl_returns_correct_values(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    today = datetime.now(UTC).date()
    _make_daily_stat(
        sqlite_session_factory, stat_date=today, net_pnl=Decimal("-150.00")
    )

    async def run() -> list[admin_stats.DailyPnlPoint]:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_daily_pnl(session, days=7)

    points = asyncio.run(run())
    assert len(points) == 1
    assert points[0].pnl == pytest.approx(-150.0)


# ---------------------------------------------------------------------------
# get_trades_summary
# ---------------------------------------------------------------------------


def test_trades_summary_empty_db_returns_zeros(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def run() -> admin_stats.TradeSummary:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_trades_summary(session)

    summary = asyncio.run(run())
    assert summary.total_trades == 0
    assert summary.win_rate == 0.0
    assert summary.profit_factor == 0.0
    assert summary.total_pnl == 0.0


def test_trades_summary_calculates_win_rate_and_pnl(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # 2 wins (+100 each), 1 loss (-50)
    _make_trade(
        sqlite_session_factory,
        status=TradeStatus.POSITION_CLOSED,
        realized_pnl=Decimal("100.00"),
    )
    _make_trade(
        sqlite_session_factory,
        status=TradeStatus.POSITION_CLOSED,
        realized_pnl=Decimal("100.00"),
    )
    _make_trade(
        sqlite_session_factory,
        status=TradeStatus.POSITION_CLOSED,
        realized_pnl=Decimal("-50.00"),
    )

    async def run() -> admin_stats.TradeSummary:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_trades_summary(session)

    summary = asyncio.run(run())
    assert summary.total_trades == 3
    assert summary.win_rate == pytest.approx(2 / 3)
    assert summary.total_pnl == pytest.approx(150.0)
    assert summary.avg_trade == pytest.approx(50.0)
    assert summary.profit_factor == pytest.approx(200.0 / 50.0)


def test_trades_summary_only_counts_closed_trades(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _make_trade(sqlite_session_factory, status=TradeStatus.POSITION_OPEN)
    _make_trade(
        sqlite_session_factory,
        status=TradeStatus.POSITION_CLOSED,
        realized_pnl=Decimal("75.00"),
    )

    async def run() -> admin_stats.TradeSummary:
        async with sqlite_session_factory() as session:
            return await admin_stats.get_trades_summary(session)

    summary = asyncio.run(run())
    assert summary.total_trades == 1
