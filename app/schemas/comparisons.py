from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.catalog import CatalogID
from app.schemas.crop_risk import RiskResponse, StorageCondition
from app.schemas.logistics import LogisticsResponse, Money, TransportOptions
from app.schemas.prices import PriceType


class MarketOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market_id: CatalogID
    transport: TransportOptions | None = None
    expected_arrival_at: AwareDatetime | None = None
    additional_market_fee_lkr: Money = Decimal(0)


class ComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    produce_batch_id: UUID
    market_ids: Annotated[list[CatalogID] | None, Field(min_length=1, max_length=20)] = None
    price_type: PriceType = "wholesale"
    storage_condition: StorageCondition = "unknown"
    assume_no_loss: bool = False
    transport: TransportOptions = Field(default_factory=TransportOptions)
    market_overrides: Annotated[list[MarketOverride], Field(max_length=20)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def unique_markets(self):
        ids = [row.market_id for row in self.market_overrides]
        if len(ids) != len(set(ids)) or (
            self.market_ids and len(self.market_ids) != len(set(self.market_ids))
        ):
            raise ValueError("Market IDs must be unique")
        if self.market_ids and not set(ids).issubset(self.market_ids):
            raise ValueError("Overrides must belong to selected markets")
        return self


class PriceEvidence(BaseModel):
    observation_id: UUID
    price_per_kg: Decimal | None
    unit: Literal["kg"] = "kg"
    currency: Literal["LKR"] = "LKR"
    price_type: PriceType
    quality: str
    observed_at: datetime
    observation_precision: str
    source_name: str
    source_url: str | None
    document_reference: str | None
    source_published_at: datetime | None
    source_publication_date: str
    imported_at: datetime
    age_hours: Decimal
    age_at_arrival_hours: Decimal | None = None
    freshness: Literal["fresh", "stale", "missing", "future"]


class Exclusion(BaseModel):
    market_id: str
    market_name: str
    code: str
    reason: str
    price: PriceEvidence | None = None
    transport: LogisticsResponse | None = None
    risk: RiskResponse | None = None


class MarketOption(BaseModel):
    rank: int = 0
    market_id: str
    market_name: str
    arrival_at: datetime
    arrival_source: Literal["user_input", "route_duration"]
    price: PriceEvidence
    transport: LogisticsResponse
    risk: RiskResponse
    quantity_basis: Literal["crop_rule", "explicit_no_loss"]
    expected_saleable_quantity_kg: Decimal
    expected_gross_revenue_lkr: Decimal
    transport_cost_lkr: Decimal
    additional_market_cost_lkr: Decimal
    total_cost_lkr: Decimal
    expected_net_proceeds_lkr: Decimal
    assumptions: list[str]


class ComparisonResponse(BaseModel):
    snapshot_id: UUID | None
    produce_batch_id: UUID
    assessed_at: datetime
    price_type: PriceType
    quality: str
    price_max_age_hours: int
    currency: Literal["LKR"] = "LKR"
    formula_version: Literal["market_comparison_v1"] = "market_comparison_v1"
    eligible: list[MarketOption]
    excluded: list[Exclusion]
    assumptions: list[str]
    snapshot_note: str
