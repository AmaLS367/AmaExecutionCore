"""add spot protection order fields

Revision ID: 4b5f6a7c8d9e
Revises: 9a1c2d3e4f5a
Create Date: 2026-04-19 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4b5f6a7c8d9e"
down_revision: Union[str, Sequence[str], None] = "9a1c2d3e4f5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("trades") as batch_op:
        batch_op.add_column(sa.Column("stop_order_link_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("stop_exchange_order_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("take_profit_order_link_id", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("take_profit_exchange_order_id", sa.String(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_trades_stop_order_link_id",
            ["stop_order_link_id"],
        )
        batch_op.create_unique_constraint(
            "uq_trades_take_profit_order_link_id",
            ["take_profit_order_link_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("trades") as batch_op:
        batch_op.drop_constraint("uq_trades_take_profit_order_link_id", type_="unique")
        batch_op.drop_constraint("uq_trades_stop_order_link_id", type_="unique")
        batch_op.drop_column("take_profit_exchange_order_id")
        batch_op.drop_column("take_profit_order_link_id")
        batch_op.drop_column("stop_exchange_order_id")
        batch_op.drop_column("stop_order_link_id")
