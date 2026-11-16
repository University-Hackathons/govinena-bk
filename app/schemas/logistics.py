from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.catalog import CatalogID

Money = Annotated[Decimal, Field(ge=0, le=1000000000, max_digits=12, decimal_places=2)]
Distance = Annotated[Decimal, Field(ge=0, le=20000, max_digits=9, decimal_places=3)]
Duration = Annotated[Decimal, Field(ge=0, le=100000, max_digits=9, decimal_places=2)]
Capacity = Annotated[Decimal, Field(gt=0, le=1000000, max_digits=10, decimal_places=3)]


class Tariff(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    base_charge_lkr: Money = Decimal(0)
    rate_per_km_lkr: Money | None = None
    loading_lkr: Money = Decimal(0)
    unloading_lkr: Money = Decimal(0)
    toll_lkr: Money = Decimal(0)
    market_handling_lkr: Money = Decimal(0)


class TransportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    route_mode: Literal["automatic", "manual"] = "automatic"
    pricing_basis: Literal["one_way", "round_trip"] = "one_way"
    transport_mode: Literal["own_vehicle", "hired_vehicle", "other"] | None = None
    vehicle_capacity_kg: Capacity | None = None
    allow_multiple_trips: bool = False
    tariff: Tariff = Field(default_factory=Tariff)
    manual_distance_km: Distance | None = None
    manual_return_distance_km: Distance | None = None
    manual_duration_minutes: Duration | None = None
    manual_return_duration_minutes: Duration | None = None
    assume_equal_return: bool = False
    known_quote_lkr: Money | None = None
    quote_note: str | None = Field(default=None, max_length=300)

    @field_validator("quote_note")
    @classmethod
    def readable_note(cls, value):
        if value is not None and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Remove control characters from the quote description")
        return value

    @model_validator(mode="after")
    def coherent(self):
        if self.known_quote_lkr is not None and (not self.quote_note or len(self.quote_note) < 3):
            raise ValueError("Describe the whole-batch quote")
        if self.pricing_basis == "one_way" and (
            self.manual_return_distance_km is not None
            or self.manual_return_duration_minutes is not None
            or self.assume_equal_return
        ):
            raise ValueError("Return fields only apply to round-trip pricing")
        if self.manual_duration_minutes is not None and self.manual_distance_km is None:
            raise ValueError("Manual duration requires a manual distance")
        if (
            self.manual_return_duration_minutes is not None
            and self.manual_return_distance_km is None
            and not (self.assume_equal_return and self.manual_distance_km is not None)
        ):
            raise ValueError("Return duration requires return distance")
        if self.manual_return_distance_km is not None and self.assume_equal_return:
            raise ValueError("Choose an explicit return distance or the equal-return assumption")
        return self


class LogisticsInput(TransportOptions):
    produce_batch_id: UUID
    market_id: CatalogID


class RouteResult(BaseModel):
    source: Literal["mapbox", "manual", "unavailable"]
    distance_km: Decimal | None = None
    duration_minutes: Decimal | None = None
    return_distance_km: Decimal | None = None
    return_duration_minutes: Decimal | None = None
    return_assumed_equal: bool = False
    cached: bool = False
    fallback_reason: str | None = None
    attribution: str | None = None


class CostBreakdown(BaseModel):
    base_charge_lkr: Decimal
    distance_charge_lkr: Decimal
    loading_lkr: Decimal
    unloading_lkr: Decimal
    toll_lkr: Decimal
    market_handling_lkr: Decimal
    billable_distance_km: Decimal
    calculated_total_lkr: Decimal


class LogisticsResponse(BaseModel):
    estimate_id: UUID | None
    status: Literal["complete", "needs_input"]
    produce_batch_id: UUID
    market_id: str
    market_name: str
    quantity_kg: Decimal
    vehicle_capacity_kg: Decimal
    trips: int
    trip_loads_kg: list[Decimal]
    route: RouteResult
    breakdown: CostBreakdown | None
    effective_cost_lkr: Decimal | None
    cost_source: Literal["calculated", "user_quote", "unavailable"]
    currency: Literal["LKR"] = "LKR"
    assumptions: dict
    messages: list[str]


class StoredEstimate(BaseModel):
    id: UUID
    produce_batch_id: UUID
    market_id: str
    market_name: str
    created_at: datetime
    assumptions: dict
    recorded_result: dict | None


class EstimateHistory(BaseModel):
    items: list[StoredEstimate]
