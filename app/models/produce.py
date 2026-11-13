from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.catalog import Crop


class ProduceBatch(Base):
    __tablename__ = "produce_batches"
    __table_args__ = (
        CheckConstraint("quantity_kg > 0 AND quantity_kg <= 1000000", name="quantity_range"),
        CheckConstraint("sell_by > harvested_at", name="selling_window"),
        CheckConstraint(
            "status IN ('available', 'reserved', 'sold', 'expired', 'archived')", name="status"
        ),
        CheckConstraint("source_latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("source_longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint(
            "(source_latitude IS NULL AND source_longitude IS NULL) OR "
            "(source_latitude IS NOT NULL AND source_longitude IS NOT NULL)",
            name="coordinate_pair",
        ),
        CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status != 'archived' AND archived_at IS NULL)",
            name="archive_state",
        ),
        Index("ix_produce_batches_farmer_status", "farmer_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    farmer_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("farmer_profiles.user_id", ondelete="RESTRICT")
    )
    crop_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("crops.id", ondelete="RESTRICT"), index=True
    )
    quantity_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    quality: Mapped[str | None] = mapped_column(String(80))
    harvested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sell_by: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_district: Mapped[str] = mapped_column(String(32))
    source_town: Mapped[str] = mapped_column(String(120))
    source_latitude: Mapped[float | None] = mapped_column(Float)
    source_longitude: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="available")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    crop: Mapped[Crop] = relationship(lazy="selectin")
