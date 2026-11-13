"""Provider-linked users and revoked sessions.

Revision ID: a01_auth_users_sessions
Revises: df69e3c200c8
"""

import sqlalchemy as sa
from alembic import op

revision = "a01_auth_users_sessions"
down_revision = "df69e3c200c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_table(
        "revoked_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "revoked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_revoked_sessions"),
    )

    # If this PostgreSQL database is hosted by Supabase, keep its public Data API
    # roles from reading backend-owned tables. The API uses the server DB owner.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE revoked_sessions ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("revoked_sessions")
    op.drop_table("users")
