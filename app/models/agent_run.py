from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_owner_created", "farmer_id", "created_at"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    farmer_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("farmer_profiles.user_id", ondelete="RESTRICT")
    )
    produce_batch_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("produce_batches.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(24), default="queued")
    approval: Mapped[str] = mapped_column(String(16), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    state: Mapped[dict] = mapped_column(JSON)
    resume_input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    checkpoint: Mapped[dict | None] = mapped_column(JSON, nullable=True)
