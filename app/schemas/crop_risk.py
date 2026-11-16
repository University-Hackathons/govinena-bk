from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

StorageCondition = Literal["unknown", "ambient", "shaded", "cooled"]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


class RiskRules(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: bool = True
    expected_selling_window_hours: Annotated[int, Field(gt=0, le=8760)]
    # Fractions of the shorter guidance/user window remaining; not biological predictions.
    high_remaining_percent: Annotated[int, Field(ge=1, le=99)] = 25
    medium_remaining_percent: Annotated[int, Field(ge=1, le=99)] = 50
    loss_percent_per_day: dict[StorageCondition, Percent] = Field(default_factory=dict)
    rationale: Annotated[str, Field(min_length=10, max_length=2000)]
    source_reference: Annotated[str | None, Field(max_length=1000)] = None

    @model_validator(mode="after")
    def thresholds(self):
        if self.high_remaining_percent >= self.medium_remaining_percent:
            raise ValueError("High threshold must be below medium threshold")
        return self


class ProfileResponse(RiskRules):
    crop_id: str
    version: int
    created_by: UUID
    created_at: datetime


class EvaluateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    produce_batch_id: UUID
    assessment_at: AwareDatetime | None = None
    expected_arrival_at: AwareDatetime | None = None
    storage_condition: StorageCondition = "unknown"

    @field_validator("assessment_at", "expected_arrival_at")
    @classmethod
    def valid_utc_range(cls, value):
        if value is None:
            return None
        try:
            return value.astimezone(UTC)
        except (OverflowError, ValueError):
            raise ValueError("Timestamp is outside the supported UTC range") from None


class RiskResponse(BaseModel):
    produce_batch_id: UUID
    crop_id: str
    assessed_at: datetime
    expected_arrival_at: datetime | None
    evaluated_at: datetime
    harvested_at: datetime
    user_deadline: datetime
    remaining_selling_seconds: float
    remaining_at_arrival_seconds: float | None
    deadline_status: Literal["open", "reached", "passed"]
    deadline_eligible: bool
    alternative_accepted: bool
    rejection_reason: (
        Literal["deadline_reached", "arrival_at_or_after_deadline", "batch_closed"] | None
    )
    urgency: Literal["low", "medium", "high"]
    profile_version: int | None
    guidance_status: Literal["configured", "missing", "disabled"]
    guidance_window_hours: int | None
    storage_condition: StorageCondition
    quantity_kg: Decimal
    estimated_saleable_quantity_kg: Decimal | None
    assumed_loss_percent: Decimal | None
    assumptions: list[str]
    formula_version: Literal["crop_risk_v1"] = "crop_risk_v1"
