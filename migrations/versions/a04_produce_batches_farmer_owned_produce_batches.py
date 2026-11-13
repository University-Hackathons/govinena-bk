"""farmer owned produce batches

Revision ID: a04_produce_batches
Revises: a03_catalog
Create Date: 2026-11-13 16:51:23.612834

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a04_produce_batches"
down_revision: str | Sequence[str] | None = "a03_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "produce_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("farmer_id", sa.Uuid(), nullable=False),
        sa.Column("crop_id", sa.String(length=64), nullable=False),
        sa.Column("quantity_kg", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("quality", sa.String(length=80), nullable=True),
        sa.Column("harvested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sell_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_district", sa.String(length=32), nullable=False),
        sa.Column("source_town", sa.String(length=120), nullable=False),
        sa.Column("source_latitude", sa.Float(), nullable=True),
        sa.Column("source_longitude", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status != 'archived' AND archived_at IS NULL)",
            name=op.f("ck_produce_batches_archive_state"),
        ),
        sa.CheckConstraint(
            "status IN ('available', 'reserved', 'sold', 'expired', 'archived')",
            name=op.f("ck_produce_batches_status"),
        ),
        sa.CheckConstraint(
            "(source_latitude IS NULL AND source_longitude IS NULL) OR "
            "(source_latitude IS NOT NULL AND source_longitude IS NOT NULL)",
            name=op.f("ck_produce_batches_coordinate_pair"),
        ),
        sa.CheckConstraint(
            "quantity_kg > 0 AND quantity_kg <= 1000000",
            name=op.f("ck_produce_batches_quantity_range"),
        ),
        sa.CheckConstraint(
            "sell_by > harvested_at", name=op.f("ck_produce_batches_selling_window")
        ),
        sa.CheckConstraint(
            "source_latitude BETWEEN -90 AND 90", name=op.f("ck_produce_batches_latitude_range")
        ),
        sa.CheckConstraint(
            "source_longitude BETWEEN -180 AND 180", name=op.f("ck_produce_batches_longitude_range")
        ),
        sa.ForeignKeyConstraint(
            ["crop_id"],
            ["crops.id"],
            name=op.f("fk_produce_batches_crop_id_crops"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["farmer_id"],
            ["farmer_profiles.user_id"],
            name=op.f("fk_produce_batches_farmer_id_farmer_profiles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_produce_batches")),
    )
    with op.batch_alter_table("produce_batches", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_produce_batches_crop_id"), ["crop_id"], unique=False)
        batch_op.create_index(
            "ix_produce_batches_farmer_status", ["farmer_id", "status"], unique=False
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE produce_batches ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("produce_batches", schema=None) as batch_op:
        batch_op.drop_index("ix_produce_batches_farmer_status")
        batch_op.drop_index(batch_op.f("ix_produce_batches_crop_id"))

    op.drop_table("produce_batches")
