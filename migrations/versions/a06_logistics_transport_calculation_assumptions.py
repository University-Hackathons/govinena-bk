"""transport calculation assumptions

Revision ID: a06_logistics
Revises: a05_market_prices
Create Date: 2026-11-15 17:31:08.749107

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a06_logistics"
down_revision: str | Sequence[str] | None = "a05_market_prices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "logistics_estimates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("farmer_id", sa.Uuid(), nullable=False),
        sa.Column("produce_batch_id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.String(length=64), nullable=False),
        sa.Column("market_name", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("recorded_result", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["farmer_id"],
            ["farmer_profiles.user_id"],
            name=op.f("fk_logistics_estimates_farmer_id_farmer_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
            name=op.f("fk_logistics_estimates_market_id_markets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["produce_batch_id"],
            ["produce_batches.id"],
            name=op.f("fk_logistics_estimates_produce_batch_id_produce_batches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_logistics_estimates")),
    )
    with op.batch_alter_table("logistics_estimates", schema=None) as batch_op:
        batch_op.create_index(
            "ix_logistics_estimates_owner_batch",
            ["farmer_id", "produce_batch_id", "created_at"],
            unique=False,
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE logistics_estimates ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("logistics_estimates", schema=None) as batch_op:
        batch_op.drop_index("ix_logistics_estimates_owner_batch")

    op.drop_table("logistics_estimates")
