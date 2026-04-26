"""add signal_submissions table

Revision ID: 2b7c9f4a1d22
Revises: 8e73b1f2a6c4
Create Date: 2026-04-14 09:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2b7c9f4a1d22"
down_revision: str | Sequence[str] | None = "8e73b1f2a6c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signal_submissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("signal_id", sa.UUID(), nullable=True),
        sa.Column("trade_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"]),
        sa.ForeignKeyConstraint(["trade_id"], ["trades.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_signal_submissions_fingerprint"),
    )


def downgrade() -> None:
    op.drop_table("signal_submissions")
