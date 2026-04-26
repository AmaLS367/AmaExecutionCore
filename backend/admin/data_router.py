from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.admin import stats as admin_stats
from backend.admin.deps import get_current_admin
from backend.config import settings
from backend.grid_engine.models import GridSession, GridSlotRecord  # noqa: F401
from backend.trade_journal.models import Trade, TradeStatus

_SECRET_FIELDS = frozenset(
    {
        "bybit_api_key",
        "bybit_api_secret",
        "bybit_testnet_api_key",
        "bybit_testnet_api_secret",
        "admin_jwt_secret",
        "database_url",
    }
)


class _NullRestClient:
    def get_wallet_balance(self) -> dict[str, object]:
        raise RuntimeError("REST client not configured.")


def make_data_router(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    rest_client: object | None = None,
) -> APIRouter:
    _rest = rest_client or _NullRestClient()

    router = APIRouter(prefix="/admin", tags=["admin-data"])

    async def _get_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    # ------------------------------------------------------------------ Stats

    @router.get("/stats/dashboard")
    async def dashboard(
        admin: str = Depends(get_current_admin),
        session: AsyncSession = Depends(_get_session),
    ) -> admin_stats.DashboardStats:
        return await admin_stats.get_dashboard_stats(session, rest_client=_rest)

    @router.get("/stats/equity-curve")
    async def equity_curve(
        admin: str = Depends(get_current_admin),
        session: AsyncSession = Depends(_get_session),
        days: int = Query(default=30, ge=1, le=365),
    ) -> list[admin_stats.EquityPoint]:
        return await admin_stats.get_equity_curve(session, days=days)

    @router.get("/stats/daily-pnl")
    async def daily_pnl(
        admin: str = Depends(get_current_admin),
        session: AsyncSession = Depends(_get_session),
        days: int = Query(default=30, ge=1, le=365),
    ) -> list[admin_stats.DailyPnlPoint]:
        return await admin_stats.get_daily_pnl(session, days=days)

    # ------------------------------------------------------------------ Trades

    @router.get("/trades/summary")
    async def trades_summary(
        admin: str = Depends(get_current_admin),
        session: AsyncSession = Depends(_get_session),
    ) -> admin_stats.TradeSummary:
        return await admin_stats.get_trades_summary(session)

    @router.get("/trades/open")
    async def trades_open(
        admin: str = Depends(get_current_admin),
        session: AsyncSession = Depends(_get_session),
    ) -> list[dict[str, Any]]:
        rows = await session.execute(
            select(Trade).where(Trade.status == TradeStatus.POSITION_OPEN)
        )
        return [_trade_to_dict(t) for t in rows.scalars()]

    @router.get("/trades")
    async def trades_list(
        admin: str = Depends(get_current_admin),
        session: AsyncSession = Depends(_get_session),
        symbol: str | None = None,
        from_date: date | None = Query(default=None),
        to_date: date | None = Query(default=None),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=200),
    ) -> dict[str, Any]:
        stmt = select(Trade).where(Trade.status == TradeStatus.POSITION_CLOSED)
        if symbol:
            stmt = stmt.where(Trade.symbol == symbol)
        if from_date:
            stmt = stmt.where(Trade.closed_at >= from_date)
        if to_date:
            stmt = stmt.where(Trade.closed_at <= to_date)

        total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        offset = (page - 1) * limit
        rows = await session.execute(
            stmt.order_by(Trade.closed_at.desc()).offset(offset).limit(limit)
        )
        items = [_trade_to_dict(t) for t in rows.scalars()]
        pages = max(1, -(-total // limit))
        return {"total": total, "page": page, "pages": pages, "items": items}

    # ------------------------------------------------------------------ Grid

    @router.get("/grid/sessions")
    async def grid_sessions(
        admin: str = Depends(get_current_admin),
        session: AsyncSession = Depends(_get_session),
    ) -> list[dict[str, Any]]:
        rows = await session.execute(select(GridSession).order_by(GridSession.id.desc()))
        return [_grid_session_to_dict(gs) for gs in rows.scalars()]

    @router.get("/grid/sessions/{session_id}")
    async def grid_session_detail(
        session_id: int,
        admin: str = Depends(get_current_admin),
        session: AsyncSession = Depends(_get_session),
    ) -> dict[str, Any]:
        gs = await session.scalar(
            select(GridSession)
            .where(GridSession.id == session_id)
            .options(selectinload(GridSession.slots))
        )
        if gs is None:
            raise HTTPException(status_code=404, detail="Grid session not found")
        return _grid_session_to_dict(gs, include_slots=True)

    # ------------------------------------------------------------------ Config

    @router.get("/config")
    async def config_view(admin: str = Depends(get_current_admin)) -> dict[str, Any]:
        data = settings.model_dump()
        for field in _SECRET_FIELDS:
            data.pop(field, None)
        return data

    @router.post("/config/reload")
    async def config_reload(admin: str = Depends(get_current_admin)) -> dict[str, bool]:
        return {"ok": True}

    return router


def _trade_to_dict(trade: Trade) -> dict[str, Any]:
    return {
        "id": str(trade.id),
        "symbol": trade.symbol,
        "signal_direction": trade.signal_direction.value,
        "exchange_side": trade.exchange_side.value,
        "market_type": trade.market_type.value,
        "mode": trade.mode.value,
        "status": trade.status.value,
        "realized_pnl": float(trade.realized_pnl) if trade.realized_pnl is not None else None,
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
        "created_at": trade.created_at.isoformat() if trade.created_at else None,
    }


def _grid_session_to_dict(gs: GridSession, *, include_slots: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": gs.id,
        "symbol": gs.symbol,
        "status": gs.status,
        "config": gs.config_json,
        "created_at": gs.created_at.isoformat() if gs.created_at else None,
        "stopped_at": gs.stopped_at.isoformat() if gs.stopped_at else None,
    }
    if include_slots:
        result["slots"] = [
            {
                "id": slot.id,
                "level": slot.level,
                "buy_price": float(slot.buy_price),
                "sell_price": float(slot.sell_price),
                "status": slot.status,
                "completed_cycles": slot.completed_cycles,
                "realized_pnl": float(slot.realized_pnl),
            }
            for slot in gs.slots
        ]
    return result
