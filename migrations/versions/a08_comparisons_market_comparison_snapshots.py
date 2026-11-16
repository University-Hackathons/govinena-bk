"""market comparison snapshots

Revision ID: a08_comparisons
Revises: a07_crop_risk
Create Date: 2026-11-16 11:39:11.845453

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a08_comparisons"
down_revision: str | Sequence[str] | None = "a07_crop_risk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "comparison_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("farmer_id", sa.Uuid(), nullable=False),
        sa.Column("produce_batch_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["farmer_id"],
            ["farmer_profiles.user_id"],
            name=op.f("fk_comparison_snapshots_farmer_id_farmer_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["produce_batch_id"],
            ["produce_batches.id"],
            name=op.f("fk_comparison_snapshots_produce_batch_id_produce_batches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comparison_snapshots")),
    )
    with op.batch_alter_table("comparison_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            "ix_comparison_snapshots_owner_batch", ["farmer_id", "produce_batch_id"], unique=False
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE comparison_snapshots ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("comparison_snapshots", schema=None) as batch_op:
        batch_op.drop_index("ix_comparison_snapshots_owner_batch")

    op.drop_table("comparison_snapshots")
