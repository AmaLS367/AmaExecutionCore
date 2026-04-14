"""merge diverged heads

Revision ID: 7c97b214b1fc
Revises: 2b7c9f4a1d22, 2c6d4b7a8e91
Create Date: 2026-04-14 12:03:34.602976

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '7c97b214b1fc'
down_revision: Union[str, Sequence[str], None] = ('2b7c9f4a1d22', '2c6d4b7a8e91')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
