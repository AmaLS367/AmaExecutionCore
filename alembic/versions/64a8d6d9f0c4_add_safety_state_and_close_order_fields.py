"""add safety state and close order fields

Revision ID: 64a8d6d9f0c4
Revises: df98e4f6725a
Create Date: 2026-04-13 12:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "64a8d6d9f0c4"
down_revision: Union[str, Sequence[str], None] = "df98e4f6725a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    trade_status = sa.Enum(
        "SIGNAL_GENERATED",
        "RISK_CALCULATED",
        "SAFETY_CHECKED",
        "ORDER_SUBMITTED",
        "ORDER_PENDING_UNKNOWN",
        "ORDER_CONFIRMED",
        "ORDER_REJECTED",
        "ORDER_CANCELLED",
        "ORDER_PARTIALLY_FILLED",
        "POSITION_OPEN",
        "POSITION_CLOSE_PENDING",
        "POSITION_CLOSED",
        "POSITION_CLOSE_FAILED",
        "PNL_RECORDED",
        name="tradestatus",
        native_enum=False,
    )
    trade_status.create(op.get_bind(), checkfirst=True)

    pause_reason = sa.Enum(
        "DAILY_LOSS",
        "WEEKLY_LOSS",
        "COOLDOWN",
        name="pausereason",
        native_enum=False,
    )
    pause_reason.create(op.get_bind(), checkfirst=True)

    op.add_column("trades", sa.Column("close_order_link_id", sa.String(length=64), nullable=True))
    op.add_column("trades", sa.Column("close_exchange_order_id", sa.String(length=64), nullable=True))
    op.add_column("trades", sa.Column("avg_exit_price", sa.Numeric(precision=18, scale=8), nullable=True))
    op.create_unique_constraint("uq_trades_close_order_link_id", "trades", ["close_order_link_id"])

    op.create_table(
        "safety_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kill_switch_active", sa.Boolean(), nullable=False),
        sa.Column("pause_reason", pause_reason, nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manual_reset_required", sa.Boolean(), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("safety_state")
    op.drop_constraint("uq_trades_close_order_link_id", "trades", type_="unique")
    op.drop_column("trades", "avg_exit_price")
    op.drop_column("trades", "close_exchange_order_id")
    op.drop_column("trades", "close_order_link_id")
