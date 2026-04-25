"""add grid engine tables

Revision ID: a6f2c8d9e1b4
Revises: 4b5f6a7c8d9e
Create Date: 2026-04-25 02:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6f2c8d9e1b4"
down_revision: str | Sequence[str] | None = "4b5f6a7c8d9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grid_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_grid_sessions_status", "grid_sessions", ["status"], unique=False)
    op.create_table(
        "grid_slot_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("buy_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("sell_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("completed_cycles", sa.Integer(), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(18, 8), nullable=False),
        sa.Column("units", sa.Numeric(18, 8), nullable=True),
        sa.Column("buy_order_id", sa.String(length=64), nullable=True),
        sa.Column("sell_order_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["grid_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_grid_slot_records_buy_order_id",
        "grid_slot_records",
        ["buy_order_id"],
        unique=False,
    )
    op.create_index(
        "ix_grid_slot_records_sell_order_id",
        "grid_slot_records",
        ["sell_order_id"],
        unique=False,
    )
    op.create_index(
        "ix_grid_slot_records_session_id",
        "grid_slot_records",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_grid_slot_records_session_id", table_name="grid_slot_records")
    op.drop_index("ix_grid_slot_records_sell_order_id", table_name="grid_slot_records")
    op.drop_index("ix_grid_slot_records_buy_order_id", table_name="grid_slot_records")
    op.drop_table("grid_slot_records")
    op.drop_index("ix_grid_sessions_status", table_name="grid_sessions")
    op.drop_table("grid_sessions")
