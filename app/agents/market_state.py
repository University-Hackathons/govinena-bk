from typing import Any, TypedDict

from app.schemas.recommendations import Status, ToolName


class MarketAgentState(TypedDict, total=False):
    """JSON state; nested payloads validated by typed tool/request/response schemas."""

    user_id: str
    produce_batch_id: str
    constraints: dict[str, Any]
    available_observations: list[dict[str, Any]]
    selected_tools: list[ToolName]
    tool_index: int
    tool_outcomes: list[dict[str, Any]]
    transport_results: list[dict[str, Any]]
    risk_results: list[dict[str, Any]]
    comparison: dict[str, Any] | None
    recommendation: dict[str, Any] | None
    explanation: str | None
    status: Status
    mode: str
    decision_summaries: list[str]
    missing_inputs: list[str]
    warnings: list[str]
    steps: int
    batch_facts: dict[str, Any]
    cycle: int
