"""durable recommendation agent runs

Revision ID: a09_agent
Revises: a08_comparisons
Create Date: 2026-11-16 12:30:39.448035

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a09_agent"
down_revision: str | Sequence[str] | None = "a08_comparisons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("farmer_id", sa.Uuid(), nullable=False),
        sa.Column("produce_batch_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("approval", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_token", sa.Uuid(), nullable=True),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("resume_input", sa.JSON(), nullable=True),
        sa.Column("checkpoint", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["farmer_id"],
            ["farmer_profiles.user_id"],
            name=op.f("fk_agent_runs_farmer_id_farmer_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["produce_batch_id"],
            ["produce_batches.id"],
            name=op.f("fk_agent_runs_produce_batch_id_produce_batches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.create_index(
            "ix_agent_runs_owner_created", ["farmer_id", "created_at"], unique=False
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_runs_owner_created")

    op.drop_table("agent_runs")
