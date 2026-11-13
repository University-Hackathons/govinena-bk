from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.catalog import CatalogID
from app.schemas.farmer import District, Name

BatchStatus = Literal["available", "reserved", "sold", "expired", "archived"]
Quantity = Annotated[Decimal, Field(gt=0, le=1000000, max_digits=10, decimal_places=3)]


def utc(value: datetime) -> datetime:
    # SQLite does not retain timezone metadata; all persisted timestamps are UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SourceLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    district: District
    town: Name
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("town")
    @classmethod
    def readable_town(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Control characters are not allowed")
        return value

    @model_validator(mode="after")
    def paired_coordinates(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both approximate coordinates or neither")
        if self.latitude is not None:
            self.latitude = round(self.latitude, 3)
            self.longitude = round(self.longitude, 3)
        return self


class BatchText(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    quality: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("quality", "notes")
    @classmethod
    def text(cls, value):
        if value is not None and any(
            ord(char) < 32 and char not in "\n\t" or ord(char) == 127 for char in value
        ):
            raise ValueError("Remove control characters")
        return value or None


def validate_dates(harvested_at: datetime, sell_by: datetime, status: str) -> None:
    now = datetime.now(UTC)
    if harvested_at > now + timedelta(minutes=5):
        raise ValueError("Harvest cannot be in the future")
    if sell_by <= harvested_at:
        raise ValueError("Sale deadline must be after harvest")
    if status in ("available", "reserved") and sell_by <= now:
        raise ValueError("Available and reserved batches require a future sale deadline")
    if status == "expired" and sell_by > now:
        raise ValueError("An expired batch must have reached its sale deadline")


class ProduceCreate(BatchText):
    crop_id: CatalogID
    quantity_kg: Quantity
    harvested_at: AwareDatetime
    sell_by: AwareDatetime
    source_location: SourceLocation | None = None
    status: BatchStatus = "available"

    @field_validator("harvested_at", "sell_by")
    @classmethod
    def normalize_utc(cls, value):
        return utc(value)

    @model_validator(mode="after")
    def chronological(self):
        validate_dates(self.harvested_at, self.sell_by, self.status)
        return self


class ProducePatch(BatchText):
    crop_id: CatalogID | None = None
    quantity_kg: Quantity | None = None
    harvested_at: AwareDatetime | None = None
    sell_by: AwareDatetime | None = None
    source_location: SourceLocation | None = None
    status: BatchStatus | None = None

    @field_validator("harvested_at", "sell_by")
    @classmethod
    def normalize_utc(cls, value):
        return utc(value) if value is not None else None

    @model_validator(mode="after")
    def nonempty_patch(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one field")
        for name in ("crop_id", "quantity_kg", "harvested_at", "sell_by", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class CropSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: CatalogID
    name_en: str
    name_si: str
    name_ta: str
    active: bool


class ProduceResponse(BatchText):
    id: UUID
    farmer_id: UUID
    crop_id: CatalogID
    crop: CropSummary
    quantity_kg: Quantity
    harvested_at: AwareDatetime
    sell_by: AwareDatetime
    source_location: SourceLocation
    status: BatchStatus
    archived_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
