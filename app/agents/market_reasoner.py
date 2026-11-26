from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.provider import create_reasoner
from app.schemas.recommendations import ToolName

ORDER = [
    "market_context",
    "price_observations",
    "transport_estimates",
    "crop_risk",
    "market_comparison",
]
PHRASES = [
    "This option has the highest estimated net proceeds among the eligible markets.",
    "The ranking uses recorded observations, not guaranteed future selling prices.",
    "The result depends on the stated transport and quantity-loss assumptions.",
    "Review the data timestamps, exclusions and hard selling deadline before deciding.",
]


class ToolPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[ToolName] = Field(min_length=1, max_length=5)


class GroundedExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentences: list[
        Literal[
            "This option has the highest estimated net proceeds among the eligible markets.",
            "The ranking uses recorded observations, not guaranteed future selling prices.",
            "The result depends on the stated transport and quantity-loss assumptions.",
            "Review the data timestamps, exclusions and hard selling deadline before deciding.",
        ]
    ] = Field(min_length=2, max_length=4)


class MarketReasoner:
    def __init__(self, settings):
        self.settings = settings

    def client(self):
        return create_reasoner(self.settings, max_retries=0, timeout=10)

    async def plan(self, context=None):
        if self.settings.reasoning_mode == "mock":
            return ToolPlan(tools=ORDER)
        return (
            await self.client()
            .with_structured_output(ToolPlan)
            .ainvoke(
                [
                    (
                        "system",
                        "Plan a farmer market comparison using only the listed read-only tools. "
                        "Select useful verification tools based on context. "
                        "The market comparison is required. No transactions exist. "
                        "Return tool selection only, no reasoning or prose.",
                    ),
                    (
                        "human",
                        f"Context flags: {context}. Select allowlisted verification tools "
                        "and the deterministic market comparison.",
                    ),
                ]
            )
        )

    async def explain(self, context):
        if self.settings.reasoning_mode == "mock":
            return GroundedExplanation(sentences=PHRASES)
        return (
            await self.client()
            .with_structured_output(GroundedExplanation)
            .ainvoke(
                [
                    (
                        "system",
                        "Compose a short explanation using only the allowed grounded sentences. "
                        "Do not invent any price, number, guarantee or action. "
                        "Do not return chain-of-thought.",
                    ),
                    (
                        "human",
                        f"Verified outcome flags: {context}. Choose useful explanatory sentences.",
                    ),
                ]
            )
        )
