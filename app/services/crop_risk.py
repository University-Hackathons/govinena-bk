from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.schemas.crop_risk import RiskResponse, RiskRules, StorageCondition
from app.schemas.produce import utc


class RiskTimeError(ValueError):
    pass


def evaluate_risk(
    *,
    batch_id: UUID,
    crop_id: str,
    quantity: Decimal,
    harvested_at: datetime,
    deadline: datetime,
    assessment_at: datetime,
    arrival_at: datetime | None,
    storage: StorageCondition,
    rules: RiskRules | None,
    profile_version: int | None,
    closed: bool = False,
) -> RiskResponse:
    """Deterministic scenario arithmetic, never a food-safety or price prediction."""
    harvest, deadline, assessment = map(utc, [harvested_at, deadline, assessment_at])
    arrival = utc(arrival_at) if arrival_at else None
    if assessment < harvest or (arrival and arrival < assessment) or deadline <= harvest:
        raise RiskTimeError("Assessment must follow harvest; arrival must not precede assessment.")
    target = arrival or assessment
    remaining = (deadline - assessment).total_seconds()
    eligible = target < deadline
    reason = (
        "batch_closed"
        if closed
        else "deadline_reached"
        if remaining <= 0
        else "arrival_at_or_after_deadline"
        if not eligible
        else None
    )
    configured = rules is not None and rules.enabled
    assumptions = [
        "Scenario guidance only: these operator assumptions are not scientifically validated "
        "predictions or food-safety advice. Inspect the actual produce.",
        "The farmer's saved sale deadline is a hard limit. Arrival at or after it is rejected; "
        "guidance never extends that deadline.",
    ]
    total_window = (deadline - harvest).total_seconds()
    fraction = Decimal(str((deadline - target).total_seconds() / total_window * 100))
    high, medium = (
        (rules.high_remaining_percent, rules.medium_remaining_percent) if configured else (25, 50)
    )
    fractions = [fraction]
    if configured:
        guidance_seconds = rules.expected_selling_window_hours * 3600
        fractions.append(
            Decimal(
                str(
                    (guidance_seconds - (target - harvest).total_seconds()) / guidance_seconds * 100
                )
            )
        )
        assumptions.append(
            f"Crop profile version {profile_version}: {rules.expected_selling_window_hours} hours "
            "from harvest is operator selling-window guidance, not a hard deadline. "
            f"{rules.rationale}"
        )
        if rules.source_reference:
            assumptions.append(f"Operator reference: {rules.source_reference}")
    else:
        assumptions.append(
            "No enabled crop profile: only the farmer deadline is assessed; "
            "no loss estimate is available."
        )
    fraction = min(fractions)
    urgency = "high" if reason or fraction <= high else "medium" if fraction <= medium else "low"
    assumptions.append(
        f"Urgency heuristic: high at or below {high}% of a window remaining; medium at or below "
        f"{medium}%. Use the more urgent of the farmer window and configured guidance window, "
        "measured at arrival when supplied. A rejected alternative is high urgency."
    )
    loss = saleable = None
    rate = rules.loss_percent_per_day.get(storage) if configured else None
    if rate is not None:
        hours = Decimal(str((target - harvest).total_seconds())) / Decimal(3600)
        loss = min(Decimal(100), rate * hours / 24)
        assumptions.append(
            f"Selected storage: {storage}. Assumed linear loss {rate}% of initial quantity per "
            "24 hours since harvest, capped at 100%; constant storage throughout is assumed. "
            "This is scenario arithmetic, not measured spoilage."
        )
        if reason is None:
            saleable = (quantity * (1 - loss / 100)).quantize(Decimal("0.001"))
        loss = loss.quantize(Decimal("0.01"))
    else:
        assumptions.append(
            f"No loss assumption for selected storage '{storage}'; saleable quantity is unknown."
        )
    if reason:
        assumptions.append(
            "This alternative is rejected. Saleable quantity is withheld; "
            "rejection does not imply physical spoilage."
        )
    return RiskResponse(
        produce_batch_id=batch_id,
        crop_id=crop_id,
        assessed_at=assessment.astimezone(UTC),
        expected_arrival_at=arrival,
        evaluated_at=target,
        harvested_at=harvest,
        user_deadline=deadline,
        remaining_selling_seconds=max(0, remaining),
        remaining_at_arrival_seconds=max(0, (deadline - arrival).total_seconds())
        if arrival
        else None,
        deadline_status="open" if remaining > 0 else "reached" if remaining == 0 else "passed",
        deadline_eligible=eligible,
        alternative_accepted=reason is None,
        rejection_reason=reason,
        urgency=urgency,
        profile_version=profile_version,
        guidance_status="configured" if configured else "disabled" if rules else "missing",
        guidance_window_hours=rules.expected_selling_window_hours if configured else None,
        storage_condition=storage,
        quantity_kg=quantity,
        estimated_saleable_quantity_kg=saleable,
        assumed_loss_percent=loss,
        assumptions=assumptions,
    )
