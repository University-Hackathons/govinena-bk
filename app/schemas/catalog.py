from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from app.schemas.farmer import District

CatalogID = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Category = Literal["vegetable", "root_vegetable"]
MarketType = Literal["dedicated_economic_centre", "wholesale_market"]


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=240)
    url: HttpUrl
    accessed_on: date
    note: str = Field(min_length=1, max_length=600)


class ReferenceRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid", allow_inf_nan=False)
    id: CatalogID
    active: bool
    sources: list[Source] = Field(min_length=1)


class CropResponse(ReferenceRecord):
    name_en: str = Field(min_length=1, max_length=120)
    name_si: str = Field(min_length=1, max_length=120)
    name_ta: str = Field(min_length=1, max_length=120)
    category: Category
    default_unit: Literal["kg"] = "kg"


class MarketResponse(ReferenceRecord):
    name: str = Field(min_length=1, max_length=160)
    district: District
    location: str = Field(min_length=1, max_length=240)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    market_type: MarketType
    coordinate_source: HttpUrl | None = None
    coordinates_verified_on: date | None = None

    @model_validator(mode="after")
    def verified_coordinates(self):
        provided = [
            self.latitude,
            self.longitude,
            self.coordinate_source,
            self.coordinates_verified_on,
        ]
        if any(value is not None for value in provided) and not all(
            value is not None for value in provided
        ):
            raise ValueError("Coordinates require a complete pair, source and verification date")
        return self


class PageResponse[T](BaseModel):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
