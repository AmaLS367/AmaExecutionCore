from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.trade_journal.models import ExitReason, TradingMode


class ClosePositionRequest(BaseModel):
    exit_reason: ExitReason = Field(default=ExitReason.MANUAL)


class ClosePositionResponse(BaseModel):
    trade_id: UUID
    status: str


class OpenPositionResponse(BaseModel):
    trade_id: UUID
    symbol: str
    direction: str
    entry_price: Decimal | None
    stop_price: Decimal | None
    target_price: Decimal | None
    qty: Decimal | None
    mode: TradingMode
    opened_at: datetime | None


class TradeListItemResponse(BaseModel):
    trade_id: UUID
    symbol: str
    direction: str
    status: str
    mode: TradingMode
    entry_price: Decimal | None
    stop_price: Decimal | None
    target_price: Decimal | None
    qty: Decimal | None
    realized_pnl: Decimal | None
    created_at: datetime
    opened_at: datetime | None
    closed_at: datetime | None


class TradeDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trade_id: UUID
    signal_id: UUID | None
    order_link_id: str | None
    exchange_order_id: str | None
    close_order_link_id: str | None
    close_exchange_order_id: str | None
    symbol: str
    signal_direction: str
    exchange_side: str
    market_type: str
    mode: TradingMode
    equity_at_entry: Decimal | None
    risk_amount_usd: Decimal | None
    risk_pct: Decimal | None
    entry_price: Decimal | None
    stop_price: Decimal | None
    target_price: Decimal | None
    expected_rrr: Decimal | None
    qty: Decimal | None
    order_type: str | None
    is_post_only: bool
    is_reduce_only: bool
    avg_fill_price: Decimal | None
    filled_qty: Decimal | None
    fee_paid: Decimal | None
    slippage: Decimal | None
    avg_exit_price: Decimal | None
    status: str
    exit_reason: str | None
    realized_pnl: Decimal | None
    pnl_pct: Decimal | None
    pnl_in_r: Decimal | None
    mae: Decimal | None
    mfe: Decimal | None
    hold_time_seconds: int | None
    opened_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
