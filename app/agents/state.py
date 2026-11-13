from datetime import datetime
from decimal import Decimal
from operator import add
from typing import Annotated, TypedDict

from app.schemas.agent import (
    DecisionRecord,
    ProfitabilityResult,
    Recommendation,
    ToolExecutionResult,
    TransportEstimate,
)
from app.schemas.market import MarketPrice


class AgentState(TypedDict, total=False):
    farmer_input: str
    crop: str
    quantity_kg: Decimal
    farmer_location: str
    production_cost: Decimal
    selling_deadline: datetime | None
    market_prices: list[MarketPrice]
    transportation_estimates: list[TransportEstimate]
    selected_tools: list[str]
    tool_execution_results: Annotated[list[ToolExecutionResult], add]
    profitability: list[ProfitabilityResult]
    recommendation: Recommendation
    decision_history: Annotated[list[DecisionRecord], add]
    re_evaluate_once: bool
    re_evaluation_count: int
    needs_re_evaluation: bool
    monitoring_status: str
