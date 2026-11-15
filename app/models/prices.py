from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PriceImport(Base):
    __tablename__ = "market_price_imports"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    imported_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    filename: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40), default="verified_csv_v1")
    verification_note: Mapped[str] = mapped_column(String(1000))
    original_file: Mapped[bytes] = mapped_column(LargeBinary)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)


class MarketPriceObservation(Base):
    __tablename__ = "market_price_observations"
    __table_args__ = (
        CheckConstraint("price_per_kg IS NULL OR price_per_kg >= 0", name="nonnegative_price"),
        CheckConstraint("price_type IN ('wholesale', 'retail', 'farmgate')", name="price_type"),
        CheckConstraint("observation_precision IN ('day', 'timestamp')", name="precision"),
        Index("ix_market_prices_lookup", "crop_id", "market_id", "price_type", "observed_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    natural_key: Mapped[str] = mapped_column(String(64), unique=True)
    crop_id: Mapped[str] = mapped_column(String(64), ForeignKey("crops.id", ondelete="RESTRICT"))
    market_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("markets.id", ondelete="RESTRICT")
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observation_precision: Mapped[str] = mapped_column(String(12))
    price_per_kg: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    price_type: Mapped[str] = mapped_column(String(12))
    quality: Mapped[str] = mapped_column(String(80), default="")
    quality_key: Mapped[str] = mapped_column(String(240))
    source_name: Mapped[str] = mapped_column(String(160))
    source_key: Mapped[str] = mapped_column(String(480))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    document_reference: Mapped[str | None] = mapped_column(String(300))
    source_publication_date: Mapped[date] = mapped_column(Date)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    import_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("market_price_imports.id", ondelete="RESTRICT")
    )
    source_row_number: Mapped[int] = mapped_column(Integer)


class PriceImportRow(Base):
    __tablename__ = "market_price_import_rows"
    __table_args__ = (
        CheckConstraint("outcome IN ('accepted', 'duplicate', 'rejected')", name="outcome"),
        Index("ix_price_import_rows_import_row", "import_id", "row_number", unique=True),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    import_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("market_price_imports.id", ondelete="RESTRICT")
    )
    row_number: Mapped[int] = mapped_column(Integer)
    raw_data: Mapped[dict] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(12))
    error_code: Mapped[str | None] = mapped_column(String(40))
    observation_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("market_price_observations.id", ondelete="RESTRICT")
    )
