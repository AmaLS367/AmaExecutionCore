"""add hard loss streak pause reason

Revision ID: 2c6d4b7a8e91
Revises: 1f0d8a9b2c3d
Create Date: 2026-04-14 10:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c6d4b7a8e91"
down_revision: str | Sequence[str] | None = "1f0d8a9b2c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing_pause_reason = sa.Enum(
        "DAILY_LOSS",
        "WEEKLY_LOSS",
        "COOLDOWN",
        name="pausereason",
        native_enum=False,
    )
    updated_pause_reason = sa.Enum(
        "DAILY_LOSS",
        "WEEKLY_LOSS",
        "COOLDOWN",
        "HARD_LOSS_STREAK",
        name="pausereason",
        native_enum=False,
    )

    with op.batch_alter_table("safety_state") as batch_op:
        batch_op.alter_column(
            "pause_reason",
            existing_type=existing_pause_reason,
            type_=updated_pause_reason,
            existing_nullable=True,
        )


def downgrade() -> None:
    existing_pause_reason = sa.Enum(
        "DAILY_LOSS",
        "WEEKLY_LOSS",
        "COOLDOWN",
        name="pausereason",
        native_enum=False,
    )
    updated_pause_reason = sa.Enum(
        "DAILY_LOSS",
        "WEEKLY_LOSS",
        "COOLDOWN",
        "HARD_LOSS_STREAK",
        name="pausereason",
        native_enum=False,
    )

    with op.batch_alter_table("safety_state") as batch_op:
        batch_op.alter_column(
            "pause_reason",
            existing_type=updated_pause_reason,
            type_=existing_pause_reason,
            existing_nullable=True,
        )
