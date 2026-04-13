import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

class SignalDirection(str, enum.Enum):
    LONG = "long"
    SHORT = "short"


class ExchangeSide(str, enum.Enum):
    BUY = "Buy"
    SELL = "Sell"


class MarketType(str, enum.Enum):
    SPOT = "spot"
    SPOT_MARGIN = "spot_margin"
    LINEAR = "linear"


class TradingMode(str, enum.Enum):
    SHADOW = "shadow"
    DEMO = "demo"
    REAL = "real"


class ExitReason(str, enum.Enum):
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    MANUAL = "manual"
    KILL_SWITCH = "kill_switch"


class TradeStatus(str, enum.Enum):
    SIGNAL_GENERATED = "signal_generated"
    RISK_CALCULATED = "risk_calculated"
    SAFETY_CHECKED = "safety_checked"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_CONFIRMED = "order_confirmed"
    ORDER_REJECTED = "order_rejected"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_PARTIALLY_FILLED = "order_partially_filled"
    POSITION_OPEN = "position_open"
    POSITION_CLOSE_PENDING = "position_close_pending"
    POSITION_CLOSED = "position_closed"
    POSITION_CLOSE_FAILED = "position_close_failed"
    PNL_RECORDED = "pnl_recorded"


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    signal_direction: Mapped[SignalDirection] = mapped_column(SAEnum(SignalDirection, native_enum=False), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    strategy_version: Mapped[str | None] = mapped_column(String(20))
    indicators_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    trades: Mapped[list["Trade"]] = relationship("Trade", back_populates="signal")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("signals.id"))
    order_link_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64))

    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    signal_direction: Mapped[SignalDirection] = mapped_column(SAEnum(SignalDirection, native_enum=False), nullable=False)
    exchange_side: Mapped[ExchangeSide] = mapped_column(SAEnum(ExchangeSide, native_enum=False), nullable=False)
    market_type: Mapped[MarketType] = mapped_column(SAEnum(MarketType, native_enum=False), nullable=False)
    mode: Mapped[TradingMode] = mapped_column(SAEnum(TradingMode, native_enum=False), nullable=False)

    # Risk snapshot
    equity_at_entry: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    risk_amount_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    risk_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    expected_rrr: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))

    # Order details
    order_type: Mapped[str | None] = mapped_column(String(20))
    is_post_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_reduce_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    filled_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    fee_paid: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    slippage: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))

    # Result
    status: Mapped[TradeStatus] = mapped_column(
        SAEnum(TradeStatus, native_enum=False),
        default=TradeStatus.SIGNAL_GENERATED,
        nullable=False,
    )
    exit_reason: Mapped[ExitReason | None] = mapped_column(SAEnum(ExitReason, native_enum=False))
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    pnl_in_r: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    mae: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    mfe: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    hold_time_seconds: Mapped[int | None] = mapped_column(Integer)

    opened_at: Mapped[datetime | None] = mapped_column()
    closed_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    signal: Mapped[Signal | None] = relationship("Signal", back_populates="trades")