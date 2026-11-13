"""Read-only reference catalog; imports remain independent of demo price fixtures."""

from datetime import date

from sqlalchemy import JSON, Boolean, CheckConstraint, Date, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Crop(Base):
    __tablename__ = "crops"
    __table_args__ = (
        CheckConstraint("default_unit = 'kg'", name="unit"),
        CheckConstraint("category IN ('vegetable', 'root_vegetable')", name="category"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(120), unique=True)
    name_si: Mapped[str] = mapped_column(String(120))
    name_ta: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32), index=True)
    default_unit: Mapped[str] = mapped_column(String(8), default="kg")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sources: Mapped[list[dict]] = mapped_column(JSON)


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL AND coordinate_source IS NULL "
            "AND coordinates_verified_on IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL AND coordinate_source IS NOT NULL "
            "AND coordinates_verified_on IS NOT NULL)",
            name="verified_coordinate_pair",
        ),
        CheckConstraint(
            "market_type IN ('dedicated_economic_centre', 'wholesale_market')", name="market_type"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    district: Mapped[str] = mapped_column(String(32), index=True)
    location: Mapped[str] = mapped_column(String(240))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    market_type: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sources: Mapped[list[dict]] = mapped_column(JSON)
    coordinate_source: Mapped[str | None] = mapped_column(String(1000))
    coordinates_verified_on: Mapped[date | None] = mapped_column(Date)
