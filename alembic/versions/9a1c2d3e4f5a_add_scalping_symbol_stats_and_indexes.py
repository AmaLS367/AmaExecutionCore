"""add scalping symbol stats and indexes

Revision ID: 9a1c2d3e4f5a
Revises: 7c97b214b1fc
Create Date: 2026-04-14 19:35:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9a1c2d3e4f5a"
down_revision: str | Sequence[str] | None = "7c97b214b1fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("daily_stats") as batch_op:
        batch_op.add_column(sa.Column("symbol_stats", sa.JSON(), nullable=True))

    op.create_index("ix_trades_status", "trades", ["status"], unique=False)
    op.create_index("ix_trades_symbol_status", "trades", ["symbol", "status"], unique=False)
    op.create_index("ix_trade_events_trade_id", "trade_events", ["trade_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_trade_events_trade_id", table_name="trade_events")
    op.drop_index("ix_trades_symbol_status", table_name="trades")
    op.drop_index("ix_trades_status", table_name="trades")

    with op.batch_alter_table("daily_stats") as batch_op:
        batch_op.drop_column("symbol_stats")
