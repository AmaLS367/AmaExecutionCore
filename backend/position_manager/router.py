from __future__ import annotations

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.position_manager.schemas import (
    ClosePositionRequest,
    ClosePositionResponse,
    OpenPositionResponse,
    TradeDetailResponse,
    TradeListItemResponse,
)
from backend.position_manager.service import PositionManagerService
from backend.trade_journal.models import Trade, TradingMode

router = APIRouter(tags=["positions", "trades"])


def get_position_manager(request: Request) -> PositionManagerService:
    return cast("PositionManagerService", request.app.state.position_manager)


@router.post("/positions/{trade_id}/close", response_model=ClosePositionResponse)
async def close_position(
    trade_id: UUID,
    request: Request,
    payload: ClosePositionRequest | None = None,
) -> ClosePositionResponse:
    service = get_position_manager(request)
    exit_reason = payload.exit_reason if payload is not None else ClosePositionRequest().exit_reason
    try:
        trade = await service.close_trade(trade_id=trade_id, exit_reason=exit_reason)
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail:
            raise HTTPException(status_code=404, detail=detail) from exc
        if "not open" in detail:
            raise HTTPException(status_code=409, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    return ClosePositionResponse(trade_id=trade.id, status=trade.status.value)


@router.get("/positions/open", response_model=list[OpenPositionResponse])
async def list_open_positions(request: Request) -> list[OpenPositionResponse]:
    trades = await get_position_manager(request).list_open_trades()
    return [
        OpenPositionResponse(
            trade_id=trade.id,
            symbol=trade.symbol,
            direction=trade.signal_direction.value,
            entry_price=trade.entry_price,
            stop_price=trade.stop_price,
            target_price=trade.target_price,
            qty=trade.qty,
            mode=trade.mode,
            opened_at=trade.opened_at,
        )
        for trade in trades
    ]


@router.get("/trades", response_model=list[TradeListItemResponse])
async def list_trades(
    request: Request,
    limit: int = Query(default=50, ge=1),
    offset: int = Query(default=0, ge=0),
    mode: TradingMode | None = Query(default=None),
) -> list[TradeListItemResponse]:
    session_factory = cast("async_sessionmaker[AsyncSession]", request.app.state.session_factory)
    async with session_factory() as session:
        statement = select(Trade).order_by(Trade.created_at.desc()).limit(limit).offset(offset)
        if mode is not None:
            statement = statement.where(Trade.mode == mode)
        result = await session.execute(statement)
        trades = result.scalars().all()
    return [
        TradeListItemResponse(
            trade_id=trade.id,
            symbol=trade.symbol,
            direction=trade.signal_direction.value,
            status=trade.status.value,
            mode=trade.mode,
            entry_price=trade.entry_price,
            stop_price=trade.stop_price,
            target_price=trade.target_price,
            qty=trade.qty,
            realized_pnl=trade.realized_pnl,
            created_at=trade.created_at,
            opened_at=trade.opened_at,
            closed_at=trade.closed_at,
        )
        for trade in trades
    ]


@router.get("/trades/{trade_id}", response_model=TradeDetailResponse)
async def get_trade_detail(trade_id: UUID, request: Request) -> TradeDetailResponse:
    session_factory = cast("async_sessionmaker[AsyncSession]", request.app.state.session_factory)
    async with session_factory() as session:
        trade = (await session.execute(select(Trade).where(Trade.id == trade_id))).scalar_one_or_none()
    if trade is None:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} was not found.")
    return TradeDetailResponse(
        trade_id=trade.id,
        signal_id=trade.signal_id,
        order_link_id=trade.order_link_id,
        exchange_order_id=trade.exchange_order_id,
        close_order_link_id=trade.close_order_link_id,
        close_exchange_order_id=trade.close_exchange_order_id,
        symbol=trade.symbol,
        signal_direction=trade.signal_direction.value,
        exchange_side=trade.exchange_side.value,
        market_type=trade.market_type.value,
        mode=trade.mode,
        equity_at_entry=trade.equity_at_entry,
        risk_amount_usd=trade.risk_amount_usd,
        risk_pct=trade.risk_pct,
        entry_price=trade.entry_price,
        stop_price=trade.stop_price,
        target_price=trade.target_price,
        expected_rrr=trade.expected_rrr,
        qty=trade.qty,
        order_type=trade.order_type,
        is_post_only=trade.is_post_only,
        is_reduce_only=trade.is_reduce_only,
        avg_fill_price=trade.avg_fill_price,
        filled_qty=trade.filled_qty,
        fee_paid=trade.fee_paid,
        slippage=trade.slippage,
        avg_exit_price=trade.avg_exit_price,
        status=trade.status.value,
        exit_reason=trade.exit_reason.value if trade.exit_reason is not None else None,
        realized_pnl=trade.realized_pnl,
        pnl_pct=trade.pnl_pct,
        pnl_in_r=trade.pnl_in_r,
        mae=trade.mae,
        mfe=trade.mfe,
        hold_time_seconds=trade.hold_time_seconds,
        opened_at=trade.opened_at,
        closed_at=trade.closed_at,
        created_at=trade.created_at,
    )
