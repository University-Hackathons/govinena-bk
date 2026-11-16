"""versioned crop perishability assumptions

Revision ID: a07_crop_risk
Revises: a06_logistics
Create Date: 2026-11-16 10:57:37.676236

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a07_crop_risk"
down_revision: str | Sequence[str] | None = "a06_logistics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "crop_risk_profiles",
        sa.Column("crop_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name=op.f("ck_crop_risk_profiles_positive_version")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_crop_risk_profiles_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["crop_id"],
            ["crops.id"],
            name=op.f("fk_crop_risk_profiles_crop_id_crops"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("crop_id", "version", name=op.f("pk_crop_risk_profiles")),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE crop_risk_profiles ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("crop_risk_profiles")
