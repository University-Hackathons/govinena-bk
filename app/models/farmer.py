from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FarmerProfile(Base):
    __tablename__ = "farmer_profiles"
    __table_args__ = (
        CheckConstraint("vehicle_capacity_kg > 0", name="positive_capacity"),
        CheckConstraint("estimated_transport_rate >= 0", name="nonnegative_rate"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="coordinate_pair",
        ),
        CheckConstraint("preferred_language IN ('en', 'si', 'ta')", name="language"),
        CheckConstraint(
            "transport_mode IN ('own_vehicle', 'hired_vehicle', 'other')",
            name="transport_mode",
        ),
    )

    # A shared primary/foreign key enforces one profile per authenticated user.
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    full_name: Mapped[str] = mapped_column(String(120))
    contact_number: Mapped[str | None] = mapped_column(String(16))
    district: Mapped[str] = mapped_column(String(32))
    town: Mapped[str] = mapped_column(String(120))
    preferred_language: Mapped[str] = mapped_column(String(2))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    transport_mode: Mapped[str] = mapped_column(String(20))
    vehicle_capacity_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    estimated_transport_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    preferred_market_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
