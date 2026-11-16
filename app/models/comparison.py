from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ComparisonSnapshot(Base):
    __tablename__ = "comparison_snapshots"
    __table_args__ = (
        Index("ix_comparison_snapshots_owner_batch", "farmer_id", "produce_batch_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    farmer_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("farmer_profiles.user_id", ondelete="RESTRICT")
    )
    produce_batch_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("produce_batches.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    inputs: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
