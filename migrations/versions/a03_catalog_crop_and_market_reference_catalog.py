"""crop and market reference catalog

Revision ID: a03_catalog
Revises: a02_farmer_profiles
Create Date: 2026-11-13 16:30:55.283912

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a03_catalog"
down_revision: str | Sequence[str] | None = "a02_farmer_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "crops",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name_en", sa.String(length=120), nullable=False),
        sa.Column("name_si", sa.String(length=120), nullable=False),
        sa.Column("name_ta", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("default_unit", sa.String(length=8), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "category IN ('vegetable', 'root_vegetable')", name=op.f("ck_crops_category")
        ),
        sa.CheckConstraint("default_unit = 'kg'", name=op.f("ck_crops_unit")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crops")),
        sa.UniqueConstraint("name_en", name=op.f("uq_crops_name_en")),
    )
    with op.batch_alter_table("crops", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_crops_category"), ["category"], unique=False)

    op.create_table(
        "markets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("district", sa.String(length=32), nullable=False),
        sa.Column("location", sa.String(length=240), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("market_type", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("coordinate_source", sa.String(length=1000), nullable=True),
        sa.Column("coordinates_verified_on", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "market_type IN ('dedicated_economic_centre', 'wholesale_market')",
            name=op.f("ck_markets_market_type"),
        ),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL AND coordinate_source IS NULL "
            "AND coordinates_verified_on IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL AND coordinate_source IS NOT NULL "
            "AND coordinates_verified_on IS NOT NULL)",
            name=op.f("ck_markets_verified_coordinate_pair"),
        ),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name=op.f("ck_markets_latitude_range")),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180", name=op.f("ck_markets_longitude_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_markets")),
        sa.UniqueConstraint("name", name=op.f("uq_markets_name")),
    )
    with op.batch_alter_table("markets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_markets_district"), ["district"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        # Only the backend database owner imports/reads these through the verified API.
        # Browser Data API roles have no write policies, even if table grants exist.
        op.execute("ALTER TABLE crops ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE markets ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("markets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_markets_district"))

    op.drop_table("markets")
    with op.batch_alter_table("crops", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_crops_category"))

    op.drop_table("crops")
