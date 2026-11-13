"""Initial infrastructure baseline

Revision ID: df69e3c200c8
Revises:
Create Date: 2026-11-04 01:39:25.361251

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "df69e3c200c8"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
