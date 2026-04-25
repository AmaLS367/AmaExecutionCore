from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class GridSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    WAITING_REENTRY = "waiting_reentry"


class GridSlotRecordStatus(str, enum.Enum):
    WAITING_BUY = "waiting_buy"
    WAITING_SELL = "waiting_sell"


class GridSession(Base):
    __tablename__ = "grid_sessions"
    __table_args__ = (Index("ix_grid_sessions_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        default=GridSessionStatus.PAUSED.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    slots: Mapped[list[GridSlotRecord]] = relationship(
        "GridSlotRecord",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="GridSlotRecord.level",
    )


class GridSlotRecord(Base):
    __tablename__ = "grid_slot_records"
    __table_args__ = (
        Index("ix_grid_slot_records_session_id", "session_id"),
        Index("ix_grid_slot_records_buy_order_id", "buy_order_id"),
        Index("ix_grid_slot_records_sell_order_id", "sell_order_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("grid_sessions.id"), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    buy_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    sell_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        default=GridSlotRecordStatus.WAITING_BUY.value,
        nullable=False,
    )
    completed_cycles: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal(0), nullable=False)
    units: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    buy_order_id: Mapped[str | None] = mapped_column(String(64))
    sell_order_id: Mapped[str | None] = mapped_column(String(64))

    session: Mapped[GridSession] = relationship("GridSession", back_populates="slots")
