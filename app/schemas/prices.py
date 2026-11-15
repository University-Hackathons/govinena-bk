from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from app.schemas.catalog import CatalogID

SL = ZoneInfo("Asia/Colombo")
PriceType = Literal["wholesale", "retail", "farmgate"]


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def key_text(value: str) -> str:
    return " ".join(value.split()).casefold()


class PriceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    crop_id: CatalogID
    market_id: CatalogID
    observed_at: AwareDatetime
    observation_precision: Literal["day", "timestamp"]
    price: Decimal | None = Field(ge=0, le=1000000000, max_digits=16, decimal_places=6)
    unit: Literal["kg", "g", "tonne"]
    unit_quantity: Decimal = Field(gt=0, le=1000000, max_digits=16, decimal_places=6)
    currency: Literal["LKR"]
    price_type: PriceType
    quality: str = Field(default="", max_length=80)
    source_name: str = Field(min_length=2, max_length=160)
    source_url: HttpUrl | None = None
    document_reference: str | None = Field(default=None, max_length=300)
    source_publication_date: date
    source_published_at: AwareDatetime | None = None

    @field_validator("quality", "source_name", "document_reference")
    @classmethod
    def readable(cls, value):
        if value is not None and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Control characters are not allowed")
        return value

    @model_validator(mode="after")
    def consistent(self):
        now = datetime.now(UTC)
        if not self.source_url and not self.document_reference:
            raise ValueError("Source URL or document reference required")
        if self.source_url and (
            self.source_url.username or self.source_url.password or len(str(self.source_url)) > 1000
        ):
            raise ValueError("Invalid source URL")
        self.observed_at = utc(self.observed_at)
        if (
            self.observation_precision == "day"
            and self.observed_at.astimezone(SL).time().isoformat() != "00:00:00"
        ):
            raise ValueError("Day-only observations must use Sri Lankan midnight")
        if self.observed_at > now + timedelta(minutes=5):
            raise ValueError("Observation cannot be in the future")
        if self.source_publication_date > now.astimezone(SL).date():
            raise ValueError("Publication cannot be in the future")
        if self.source_publication_date < self.observed_at.astimezone(SL).date():
            raise ValueError("Publication precedes observation")
        if self.source_published_at:
            self.source_published_at = utc(self.source_published_at)
            if (
                self.source_published_at > now + timedelta(minutes=5)
                or self.source_published_at < self.observed_at
            ):
                raise ValueError("Inconsistent publication timestamp")
            if self.source_published_at.astimezone(SL).date() != self.source_publication_date:
                raise ValueError("Publication date and timestamp disagree")
        return self


class ObservationResponse(BaseModel):
    series_key: str
    id: UUID
    crop_id: str
    crop_name: str
    market_id: str
    market_name: str
    observed_at: AwareDatetime
    observation_precision: Literal["day", "timestamp"]
    price_per_kg: Decimal | None
    currency: Literal["LKR"] = "LKR"
    unit: Literal["kg"] = "kg"
    price_type: PriceType
    quality: str
    source_name: str
    source_url: str | None
    document_reference: str | None
    source_publication_date: date
    source_published_at: AwareDatetime | None
    imported_at: AwareDatetime
    import_id: UUID
    freshness: Literal["fresh", "stale", "missing"]
    age_hours: float


class PricePage(BaseModel):
    items: list[ObservationResponse]
    total: int
    page: int
    page_size: int
    as_of: AwareDatetime
    stale_after_hours: int


class HistoryResponse(BaseModel):
    items: list[ObservationResponse]
    total: int
    truncated: bool
    start_date: date
    end_date: date


class ComparisonItem(BaseModel):
    market_id: str
    market_name: str
    district: str
    observations: list[ObservationResponse]


class ComparisonResponse(BaseModel):
    items: list[ComparisonItem]
    total: int
    page: int
    page_size: int
    as_of: AwareDatetime


class FreshnessResponse(BaseModel):
    as_of: AwareDatetime
    stale_after_hours: int
    fresh_series: int
    stale_series: int
    missing_series: int
    expected_market_crop_pairs: int
    pairs_without_reported_price: int
    last_observed_at: AwareDatetime | None
    last_imported_at: AwareDatetime | None


class ImportReport(BaseModel):
    id: UUID
    sha256: str
    filename: str
    provider: str
    imported_by: UUID
    imported_at: AwareDatetime
    verification_note: str
    accepted: int
    duplicates: int
    rejected: int
    replayed: bool = False


class ImportRowResponse(BaseModel):
    row_number: int
    outcome: str
    error_code: str | None
    observation_id: UUID | None
    raw_data: dict


class ImportAudit(BaseModel):
    report: ImportReport
    items: list[ImportRowResponse]
    total: int
    page: int
    page_size: int
