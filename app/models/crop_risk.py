from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CropRiskProfile(Base):
    """Append-only operator assumptions. The highest version is authoritative."""

    __tablename__ = "crop_risk_profiles"
    __table_args__ = (CheckConstraint("version > 0", name="positive_version"),)
    crop_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("crops.id", ondelete="RESTRICT"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    rules: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
