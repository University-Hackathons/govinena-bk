from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LogisticsEstimate(Base):
    __tablename__ = "logistics_estimates"
    __table_args__ = (
        Index("ix_logistics_estimates_owner_batch", "farmer_id", "produce_batch_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    farmer_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("farmer_profiles.user_id", ondelete="RESTRICT")
    )
    produce_batch_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("produce_batches.id", ondelete="RESTRICT")
    )
    market_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("markets.id", ondelete="RESTRICT")
    )
    market_name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    assumptions: Mapped[dict] = mapped_column(JSON)
    recorded_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
