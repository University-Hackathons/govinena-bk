"""Farmer profiles and preferences.

Revision ID: a02_farmer_profiles
Revises: a01_auth_users_sessions
"""

import sqlalchemy as sa
from alembic import op

revision = "a02_farmer_profiles"
down_revision = "a01_auth_users_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "farmer_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("contact_number", sa.String(16), nullable=True),
        sa.Column("district", sa.String(32), nullable=False),
        sa.Column("town", sa.String(120), nullable=False),
        sa.Column("preferred_language", sa.String(2), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("transport_mode", sa.String(20), nullable=False),
        sa.Column("vehicle_capacity_kg", sa.Numeric(10, 2), nullable=True),
        sa.Column("estimated_transport_rate", sa.Numeric(10, 2), nullable=True),
        sa.Column("preferred_market_ids", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_farmer_profiles"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("vehicle_capacity_kg > 0", name="positive_capacity"),
        sa.CheckConstraint("estimated_transport_rate >= 0", name="nonnegative_rate"),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="coordinate_pair",
        ),
        sa.CheckConstraint("preferred_language IN ('en', 'si', 'ta')", name="language"),
        sa.CheckConstraint(
            "transport_mode IN ('own_vehicle', 'hired_vehicle', 'other')",
            name="transport_mode",
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE farmer_profiles ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("farmer_profiles")
