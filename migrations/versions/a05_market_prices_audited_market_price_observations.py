"""audited market price observations

Revision ID: a05_market_prices
Revises: a04_produce_batches
Create Date: 2026-11-15 16:25:22.056166

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a05_market_prices"
down_revision: str | Sequence[str] | None = "a04_produce_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "market_price_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("imported_by", sa.Uuid(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filename", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("verification_note", sa.String(length=1000), nullable=False),
        sa.Column("original_file", sa.LargeBinary(), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["imported_by"],
            ["users.id"],
            name=op.f("fk_market_price_imports_imported_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_price_imports")),
        sa.UniqueConstraint("sha256", name=op.f("uq_market_price_imports_sha256")),
    )
    op.create_table(
        "market_price_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("natural_key", sa.String(length=64), nullable=False),
        sa.Column("crop_id", sa.String(length=64), nullable=False),
        sa.Column("market_id", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_precision", sa.String(length=12), nullable=False),
        sa.Column("price_per_kg", sa.Numeric(precision=16, scale=6), nullable=True),
        sa.Column("price_type", sa.String(length=12), nullable=False),
        sa.Column("quality", sa.String(length=80), nullable=False),
        sa.Column("quality_key", sa.String(length=240), nullable=False),
        sa.Column("source_name", sa.String(length=160), nullable=False),
        sa.Column("source_key", sa.String(length=480), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("document_reference", sa.String(length=300), nullable=True),
        sa.Column("source_publication_date", sa.Date(), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "observation_precision IN ('day', 'timestamp')",
            name=op.f("ck_market_price_observations_precision"),
        ),
        sa.CheckConstraint(
            "price_type IN ('wholesale', 'retail', 'farmgate')",
            name=op.f("ck_market_price_observations_price_type"),
        ),
        sa.CheckConstraint(
            "price_per_kg IS NULL OR price_per_kg >= 0",
            name=op.f("ck_market_price_observations_nonnegative_price"),
        ),
        sa.ForeignKeyConstraint(
            ["crop_id"],
            ["crops.id"],
            name=op.f("fk_market_price_observations_crop_id_crops"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["market_price_imports.id"],
            name=op.f("fk_market_price_observations_import_id_market_price_imports"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
            name=op.f("fk_market_price_observations_market_id_markets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_price_observations")),
        sa.UniqueConstraint("natural_key", name=op.f("uq_market_price_observations_natural_key")),
    )
    with op.batch_alter_table("market_price_observations", schema=None) as batch_op:
        batch_op.create_index(
            "ix_market_prices_lookup",
            ["crop_id", "market_id", "price_type", "observed_at"],
            unique=False,
        )

    op.create_table(
        "market_price_import_rows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(length=12), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("observation_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'duplicate', 'rejected')",
            name=op.f("ck_market_price_import_rows_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["market_price_imports.id"],
            name=op.f("fk_market_price_import_rows_import_id_market_price_imports"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["market_price_observations.id"],
            name=op.f("fk_market_price_import_rows_observation_id_market_price_observations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_price_import_rows")),
    )
    with op.batch_alter_table("market_price_import_rows", schema=None) as batch_op:
        batch_op.create_index(
            "ix_price_import_rows_import_row", ["import_id", "row_number"], unique=True
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE market_price_imports ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE market_price_observations ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE market_price_import_rows ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("market_price_import_rows", schema=None) as batch_op:
        batch_op.drop_index("ix_price_import_rows_import_row")

    op.drop_table("market_price_import_rows")
    with op.batch_alter_table("market_price_observations", schema=None) as batch_op:
        batch_op.drop_index("ix_market_prices_lookup")

    op.drop_table("market_price_observations")
    op.drop_table("market_price_imports")
