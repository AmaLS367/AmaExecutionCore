from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.trade_journal.models import (
    DailyStat,
    SafetyState,
    Trade,
    TradeStatus,
)


class DashboardStats(BaseModel):
    equity: float
    trading_mode: str
    safety_guard_status: str
    open_positions_count: int
    pnl_today: float


class EquityPoint(BaseModel):
    date: date
    equity: float


class DailyPnlPoint(BaseModel):
    date: date
    pnl: float


class TradeSummary(BaseModel):
    total_trades: int
    win_rate: float
    profit_factor: float
    total_pnl: float
    avg_trade: float


async def get_dashboard_stats(session: AsyncSession, *, rest_client: object) -> DashboardStats:
    if settings.trading_mode == "shadow":
        equity = settings.shadow_equity
    else:
        balance = rest_client.get_wallet_balance()  # type: ignore[attr-defined]
        try:
            equity = float(balance["list"][0].get("totalWalletBalance", 0.0))
        except (KeyError, IndexError, TypeError):
            equity = 0.0

    safety_state = await session.scalar(select(SafetyState).where(SafetyState.id == 1))
    if safety_state is None:
        safety_guard_status = "OK"
    elif safety_state.kill_switch_active:
        safety_guard_status = "KILLED"
    elif safety_state.pause_reason is not None:
        safety_guard_status = "PAUSED"
    else:
        safety_guard_status = "OK"

    open_count = await session.scalar(
        select(func.count()).select_from(Trade).where(Trade.status == TradeStatus.POSITION_OPEN),
    )

    today = datetime.now(UTC).date()
    daily_stat = await session.scalar(select(DailyStat).where(DailyStat.stat_date == today))
    pnl_today = float(daily_stat.net_pnl) if daily_stat and daily_stat.net_pnl is not None else 0.0

    return DashboardStats(
        equity=equity,
        trading_mode=settings.trading_mode,
        safety_guard_status=safety_guard_status,
        open_positions_count=int(open_count or 0),
        pnl_today=pnl_today,
    )


async def get_equity_curve(session: AsyncSession, *, days: int = 30) -> list[EquityPoint]:
    cutoff = datetime.now(UTC).date() - timedelta(days=days - 1)
    rows = await session.execute(
        select(DailyStat.stat_date, DailyStat.ending_equity)
        .where(DailyStat.stat_date >= cutoff)
        .where(DailyStat.ending_equity.is_not(None))
        .order_by(DailyStat.stat_date),
    )
    return [EquityPoint(date=r.stat_date, equity=float(r.ending_equity)) for r in rows]


async def get_daily_pnl(session: AsyncSession, *, days: int = 30) -> list[DailyPnlPoint]:
    cutoff = datetime.now(UTC).date() - timedelta(days=days - 1)
    rows = await session.execute(
        select(DailyStat.stat_date, DailyStat.net_pnl)
        .where(DailyStat.stat_date >= cutoff)
        .where(DailyStat.net_pnl.is_not(None))
        .order_by(DailyStat.stat_date),
    )
    return [DailyPnlPoint(date=r.stat_date, pnl=float(r.net_pnl)) for r in rows]


async def get_trades_summary(session: AsyncSession) -> TradeSummary:
    rows = await session.execute(
        select(Trade.realized_pnl)
        .where(Trade.status == TradeStatus.POSITION_CLOSED)
        .where(Trade.realized_pnl.is_not(None)),
    )
    pnls = [float(r.realized_pnl) for r in rows]

    if not pnls:
        return TradeSummary(
            total_trades=0,
            win_rate=0.0,
            profit_factor=0.0,
            total_pnl=0.0,
            avg_trade=0.0,
        )

    total = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    return TradeSummary(
        total_trades=total,
        win_rate=len(wins) / total,
        profit_factor=profit_factor,
        total_pnl=sum(pnls),
        avg_trade=sum(pnls) / total,
    )
