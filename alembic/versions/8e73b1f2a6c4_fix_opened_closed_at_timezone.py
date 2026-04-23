"""fix opened_at and closed_at timezone

Revision ID: 8e73b1f2a6c4
Revises: 64a8d6d9f0c4
Create Date: 2026-04-13 15:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8e73b1f2a6c4"
down_revision: str | Sequence[str] | None = "64a8d6d9f0c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "trades",
        "opened_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="opened_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "trades",
        "closed_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        postgresql_using="closed_at AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    op.alter_column(
        "trades",
        "closed_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
        postgresql_using="closed_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "trades",
        "opened_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
        postgresql_using="opened_at AT TIME ZONE 'UTC'",
    )
