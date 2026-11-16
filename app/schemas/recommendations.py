from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.catalog import CatalogID
from app.schemas.comparisons import ComparisonInput, ComparisonResponse, MarketOverride

ToolName = Literal[
    "market_context", "price_observations", "transport_estimates", "crop_risk", "market_comparison"
]
Status = Literal["queued", "running", "needs_input", "completed", "failed"]


class AgentConstraints(ComparisonInput):
    market_ids: Annotated[list[CatalogID] | None, Field(min_length=1, max_length=5)] = None
    market_overrides: Annotated[list[MarketOverride], Field(max_length=5)] = Field(
        default_factory=list
    )


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    constraints: AgentConstraints | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    action: Literal["accept", "reject"]


class ToolTrace(BaseModel):
    tool: ToolName
    status: Literal["success", "failed", "needs_input"]
    attempt: int
    summary: str
    completed_at: datetime


class RecommendationResult(BaseModel):
    market_id: str
    market_name: str
    expected_net_proceeds_lkr: str
    explanation: str


class RunResponse(BaseModel):
    id: UUID
    produce_batch_id: UUID
    status: Status
    approval: Literal["pending", "accepted", "rejected"]
    revision: int
    created_at: datetime
    updated_at: datetime
    constraints: AgentConstraints
    mode: Literal["mock", "openai"]
    selected_tools: list[ToolName] = Field(default_factory=list)
    tool_outcomes: list[ToolTrace] = Field(default_factory=list)
    decision_summaries: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    comparison: ComparisonResponse | None = None
    recommendation: RecommendationResult | None = None
    steps: int = 0
