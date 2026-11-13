import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

District = Literal[
    "Ampara",
    "Anuradhapura",
    "Badulla",
    "Batticaloa",
    "Colombo",
    "Galle",
    "Gampaha",
    "Hambantota",
    "Jaffna",
    "Kalutara",
    "Kandy",
    "Kegalle",
    "Kilinochchi",
    "Kurunegala",
    "Mannar",
    "Matale",
    "Matara",
    "Monaragala",
    "Mullaitivu",
    "Nuwara Eliya",
    "Polonnaruwa",
    "Puttalam",
    "Ratnapura",
    "Trincomalee",
    "Vavuniya",
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
MarketID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]


class FarmerProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    full_name: Name
    contact_number: str | None = Field(default=None, max_length=40)
    district: District
    town: Name
    preferred_language: Literal["en", "si", "ta"]
    latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        description="Optional approximate latitude, rounded to 3 decimals",
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        description="Optional approximate longitude, rounded to 3 decimals",
    )
    transport_mode: Literal["own_vehicle", "hired_vehicle", "other"]
    vehicle_capacity_kg: Decimal | None = Field(
        default=None,
        gt=0,
        description="Optional vehicle capacity in kilograms",
        le=1000000,
        max_digits=10,
        decimal_places=2,
    )
    estimated_transport_rate: Decimal | None = Field(
        default=None,
        ge=0,
        description="Optional estimated transport rate in LKR per kilometre",
        le=1000000,
        max_digits=10,
        decimal_places=2,
    )
    preferred_market_ids: list[MarketID] = Field(default_factory=list, max_length=20)

    @field_validator("full_name", "town")
    @classmethod
    def readable_text(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Control characters are not allowed")
        return value

    @field_validator("contact_number")
    @classmethod
    def contact(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = re.sub(r"[\s()\-]", "", value)
        if not re.fullmatch(r"(?:0[1-9][0-9]{8}|\+[1-9][0-9]{7,14})", value):
            raise ValueError("Use a Sri Lankan local number or international +country number")
        return value

    @field_validator("preferred_market_ids")
    @classmethod
    def unique_markets(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def approximate_location(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both approximate coordinates or leave both empty")
        if self.latitude is not None:
            self.latitude = round(self.latitude, 3)
            self.longitude = round(self.longitude, 3)
        return self


class ProfileCompletion(BaseModel):
    required_complete: bool
    optional_completed: int
    optional_total: int
    percentage: int


class FarmerProfileResponse(FarmerProfileInput):
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    completion: ProfileCompletion
