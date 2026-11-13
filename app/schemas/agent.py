from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.market import SYNTHETIC_NOTICE, MarketPrice

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]


class AgentDemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    farmer_input: str = Field(default="Compare the synthetic demo markets", max_length=1000)
    crop: Literal["tomato"] = "tomato"
    quantity_kg: Decimal = Field(default=Decimal("100"), gt=0, le=100000, decimal_places=3)
    farmer_location: str = Field(default="Synthetic Farm", min_length=1, max_length=200)
    production_cost: Money = Decimal("0.00")
    selling_deadline: AwareDatetime | None = None
    re_evaluate_once: bool = False


class TransportEstimate(BaseModel):
    market_id: str
    distance_km: Decimal
    cost: Money
    data_source: Literal["synthetic"] = "synthetic"


class ProfitabilityResult(BaseModel):
    market_id: str
    revenue: Decimal
    transport_cost: Decimal
    production_cost: Decimal
    estimated_profit: Decimal
    currency: Literal["LKR"] = "LKR"


class Recommendation(BaseModel):
    market_id: str
    estimated_profit: Decimal
    explanation: str
    data_source: Literal["synthetic"] = "synthetic"


class DecisionRecord(BaseModel):
    stage: str
    summary: str


class ToolExecutionResult(BaseModel):
    tool: str
    status: Literal["synthetic_success"] = "synthetic_success"
    summary: str


class AgentDemoResponse(BaseModel):
    data_source: Literal["synthetic"] = "synthetic"
    reasoning_mode: Literal["mock"] = "mock"
    notice: str = SYNTHETIC_NOTICE
    farmer_input: str
    crop: str
    quantity_kg: Decimal
    farmer_location: str
    selling_deadline: datetime | None
    market_prices: list[MarketPrice]
    transportation_estimates: list[TransportEstimate]
    tool_execution_results: list[ToolExecutionResult]
    profitability: list[ProfitabilityResult]
    recommendation: Recommendation
    decision_history: list[DecisionRecord]
    re_evaluation_count: int
    monitoring_status: Literal["demo_complete_no_background_monitoring"]
