from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.catalog import Market
from app.models.comparison import ComparisonSnapshot
from app.models.crop_risk import CropRiskProfile
from app.models.prices import MarketPriceObservation as Observation
from app.repositories.produce import ProduceRepository
from app.schemas.comparisons import (
    ComparisonInput,
    ComparisonResponse,
    Exclusion,
    MarketOption,
    MarketOverride,
    PriceEvidence,
)
from app.schemas.crop_risk import RiskRules
from app.schemas.logistics import LogisticsInput
from app.schemas.prices import SL, key_text
from app.schemas.produce import utc
from app.services.crop_risk import RiskTimeError, evaluate_risk
from app.services.logistics import LogisticsService
from app.services.net_return import gross_revenue, net_proceeds, total_cost
from app.services.transport_cost import LogisticsValidationError


class MarketComparisonService:
    """Observed-price scenarios, independent of LangGraph and LLMs."""

    def __init__(self, session: Session, logistics: LogisticsService, price_max_age_hours=72):
        self.session, self.logistics, self.max_age = session, logistics, price_max_age_hours

    def evidence(self, row, now, arrival=None):
        age = Decimal(str((now - utc(row.observed_at)).total_seconds())) / 3600
        arrival_age = (
            Decimal(str((arrival - utc(row.observed_at)).total_seconds())) / 3600
            if arrival
            else None
        )
        future = (
            utc(row.observed_at) > now
            or utc(row.imported_at) > now
            or (row.source_published_at and utc(row.source_published_at) > now)
            or row.source_publication_date > now.astimezone(SL).date()
        )
        freshness = (
            "future"
            if future
            else "missing"
            if row.price_per_kg is None
            else "stale"
            if max(age, arrival_age or age) > self.max_age
            else "fresh"
        )
        return PriceEvidence(
            observation_id=row.id,
            price_per_kg=row.price_per_kg,
            price_type=row.price_type,
            quality=row.quality,
            observed_at=utc(row.observed_at),
            observation_precision=row.observation_precision,
            source_name=row.source_name,
            source_url=row.source_url,
            document_reference=row.document_reference,
            source_published_at=utc(row.source_published_at) if row.source_published_at else None,
            source_publication_date=row.source_publication_date.isoformat(),
            imported_at=utc(row.imported_at),
            age_hours=age.quantize(Decimal("0.001")),
            age_at_arrival_hours=arrival_age.quantize(Decimal("0.001"))
            if arrival_age is not None
            else None,
            freshness=freshness,
        )

    def select_price(self, batch, market_id, price_type, now):
        rows = self.session.scalars(
            select(Observation)
            .where(
                Observation.crop_id == batch.crop_id,
                Observation.market_id == market_id,
                Observation.price_type == price_type,
                Observation.quality_key == key_text(batch.quality or ""),
            )
            .order_by(
                Observation.observed_at.desc(),
                Observation.imported_at.desc(),
                Observation.source_key,
                Observation.id,
            )
        ).all()
        # Latest known report per source. A missing report never revives that source's older price.
        latest = {}
        for row in rows:
            if self.evidence(row, now).freshness != "future":
                latest.setdefault(row.source_key, row)
        fresh = [row for row in latest.values() if self.evidence(row, now).freshness == "fresh"]
        if fresh:
            return fresh[0], None
        row = next(iter(latest.values()), rows[0] if rows else None)
        return row, (
            "no_compatible_price" if row is None else f"price_{self.evidence(row, now).freshness}"
        )

    async def compare(
        self,
        farmer_id: UUID,
        data: ComparisonInput,
        *,
        assessed_at: datetime | None = None,
        persist: bool = True,
    ):
        try:
            return await self._compare(
                farmer_id, data, utc(assessed_at or datetime.now(UTC)), persist
            )
        except SQLAlchemyError:
            self.session.rollback()
            raise HTTPException(503) from None

    async def _compare(self, farmer_id, data, now, persist=True):
        batch = ProduceRepository(self.session).get(farmer_id, data.produce_batch_id)
        if batch is None:
            raise HTTPException(404)
        if now < utc(batch.harvested_at):
            raise HTTPException(422)
        markets = (
            self.session.scalars(
                select(Market).where(Market.active.is_(True)).order_by(Market.id)
            ).all()
            if data.market_ids is None
            else [self.session.get(Market, id) for id in data.market_ids]
        )
        if len(markets) > 20:
            raise HTTPException(422)
        selected_ids = set(data.market_ids or [market.id for market in markets])
        if any(row.market_id not in selected_ids for row in data.market_overrides):
            raise HTTPException(422)
        overrides = {row.market_id: row for row in data.market_overrides}
        profile = self.session.scalar(
            select(CropRiskProfile)
            .where(CropRiskProfile.crop_id == batch.crop_id)
            .order_by(CropRiskProfile.version.desc())
            .limit(1)
        )
        rules = RiskRules.model_validate(profile.rules) if profile else None
        eligible, excluded = [], []
        used_mapbox = False
        for index, market in enumerate(markets):
            market_id = market.id if market else data.market_ids[index]
            name = market.name if market else market_id
            context = dict(market_id=market_id, market_name=name)

            def exclude(code, reason, context=context, **details):
                excluded.append(Exclusion(**context, code=code, reason=reason, **details))

            if market is None or not market.active:
                exclude("market_unavailable", "This market is unknown or inactive.")
                continue
            if not batch.crop.active or batch.crop.default_unit != "kg":
                exclude(
                    "crop_unavailable",
                    "The crop is inactive or its unit is incompatible with kg prices.",
                )
                continue
            if batch.status in {"sold", "archived"} or now >= utc(batch.sell_by):
                exclude(
                    "batch_not_sellable",
                    "The batch is sold, archived, or its hard selling deadline has been reached.",
                )
                continue
            row, error = self.select_price(batch, market_id, data.price_type, now)
            evidence = self.evidence(row, now) if row else None
            if error:
                reasons = {
                    "no_compatible_price": (
                        "No observation matches the crop, price type and exact grade "
                        "(unspecified grades only match unspecified grades)."
                    ),
                    "price_missing": (
                        "The latest compatible source reports no price; missing is not zero."
                    ),
                    "price_stale": (
                        "The latest compatible price is older than the configured freshness limit."
                    ),
                    "price_future": (
                        "Only future-dated or not-yet-available observations exist; they "
                        "cannot be used as known prices."
                    ),
                }
                exclude(error, reasons[error], price=evidence)
                continue
            override = overrides.get(market_id, MarketOverride(market_id=market_id))
            options = override.transport or data.transport
            try:
                transport = await self.logistics.estimate(
                    farmer_id,
                    LogisticsInput(
                        **options.model_dump(),
                        produce_batch_id=batch.id,
                        market_id=market_id,
                    ),
                    persist=False,
                )
            except LogisticsValidationError as exc:
                exclude("transport_validation", exc.message, price=evidence)
                continue
            used_mapbox |= transport.route.source == "mapbox"
            if transport.effective_cost_lkr is None:
                exclude(
                    "transport_cost_unknown",
                    (
                        "Transport cost is unknown. Enter a known distance and rate or a "
                        "whole-batch quote."
                    ),
                    price=evidence,
                    transport=transport,
                )
                continue
            arrival_source = "user_input" if override.expected_arrival_at else "route_duration"
            try:
                arrival = (
                    utc(override.expected_arrival_at)
                    if override.expected_arrival_at
                    else now + timedelta(minutes=float(transport.route.duration_minutes))
                    if transport.route.duration_minutes is not None and transport.trips == 1
                    else None
                )
            except (OverflowError, ValueError):
                arrival = None
            if arrival is None:
                exclude(
                    "arrival_unknown",
                    (
                        "Enter the expected final arrival time. A quote or distance alone "
                        "does not establish travel time; multiple deliveries require an "
                        "explicit final arrival."
                    ),
                    price=evidence,
                    transport=transport,
                )
                continue
            try:
                risk = evaluate_risk(
                    batch_id=batch.id,
                    crop_id=batch.crop_id,
                    quantity=batch.quantity_kg,
                    harvested_at=batch.harvested_at,
                    deadline=batch.sell_by,
                    assessment_at=now,
                    arrival_at=arrival,
                    storage=data.storage_condition,
                    rules=rules,
                    profile_version=profile.version if profile else None,
                )
            except RiskTimeError:
                exclude(
                    "arrival_invalid",
                    "Expected arrival must not precede the current assessment time.",
                    price=evidence,
                    transport=transport,
                )
                continue
            if not risk.alternative_accepted:
                exclude(
                    "deadline_violated",
                    "Arrival is at or after the farmer's hard selling deadline.",
                    price=evidence,
                    transport=transport,
                    risk=risk,
                )
                continue
            evidence = self.evidence(row, now, arrival)
            if evidence.freshness != "fresh":
                exclude(
                    "price_stale_at_arrival",
                    (
                        "The observed price would exceed the configured freshness limit at "
                        "the expected arrival time."
                    ),
                    price=evidence,
                    transport=transport,
                    risk=risk,
                )
                continue
            quantity = risk.estimated_saleable_quantity_kg
            basis = "crop_rule"
            assumptions = [
                (
                    "Revenue holds the selected historical observation constant as a "
                    "scenario; the future selling price is unknown and this is not a "
                    "forecast."
                ),
                (
                    "Exact crop, kg unit, price type and grade matching is required. "
                    "Latest known non-missing fresh source is chosen by observation time, "
                    "import time, source key and ID, never by highest price."
                ),
                (
                    "Additional market fees are whole-batch user inputs, separate from "
                    "all fees already included in transport. Unentered flat fees default "
                    "to zero as a disclosed scenario assumption."
                ),
            ]
            if data.assume_no_loss:
                quantity, basis = batch.quantity_kg, "explicit_no_loss"
                assumptions.append(
                    "Farmer explicitly selected no quantity loss; this overrides crop "
                    "loss arithmetic for this scenario only, never the hard deadline."
                )
            if quantity is None:
                exclude(
                    "loss_assumption_missing",
                    (
                        "No applicable loss assumption exists. Configure a crop storage rule "
                        "or explicitly select a no-loss scenario."
                    ),
                    price=evidence,
                    transport=transport,
                    risk=risk,
                )
                continue
            if quantity <= 0:
                exclude(
                    "no_saleable_quantity",
                    "The selected assumptions leave no saleable quantity.",
                    price=evidence,
                    transport=transport,
                    risk=risk,
                )
                continue
            gross = gross_revenue(quantity, row.price_per_kg)
            costs = total_cost(transport.effective_cost_lkr, override.additional_market_fee_lkr)
            eligible.append(
                MarketOption(
                    **context,
                    arrival_at=arrival,
                    arrival_source=arrival_source,
                    price=evidence,
                    transport=transport,
                    risk=risk,
                    quantity_basis=basis,
                    expected_saleable_quantity_kg=quantity,
                    expected_gross_revenue_lkr=gross,
                    transport_cost_lkr=transport.effective_cost_lkr,
                    additional_market_cost_lkr=override.additional_market_fee_lkr,
                    total_cost_lkr=costs,
                    expected_net_proceeds_lkr=net_proceeds(gross, costs),
                    assumptions=assumptions,
                )
            )
        eligible.sort(key=lambda item: (-item.expected_net_proceeds_lkr, item.market_id))
        for rank, option in enumerate(eligible, 1):
            option.rank = rank
        excluded.sort(key=lambda item: item.market_id)
        snapshot_id = None if used_mapbox or not persist else uuid4()
        result = ComparisonResponse(
            snapshot_id=snapshot_id,
            produce_batch_id=batch.id,
            assessed_at=now,
            price_type=data.price_type,
            quality=batch.quality or "",
            price_max_age_hours=self.max_age,
            eligible=eligible,
            excluded=excluded,
            assumptions=[
                (
                    "Observed-price scenarios only. Sale, buyer acceptance, actual grade "
                    "and future prices are not guaranteed."
                ),
                (
                    "Prices must be fresh both now and at expected arrival. All money "
                    "uses Decimal with ROUND_HALF_UP to cents; negative net proceeds are "
                    "retained."
                ),
                "Market ID breaks equal-net ties. At most 20 candidate markets per request.",
            ],
            snapshot_note=(
                "Comparison not retained because it includes Mapbox route output or derived values."
            )
            if used_mapbox
            else "Comparison evaluated without a separate snapshot."
            if not persist
            else (
                "Private comparison snapshot saved; it is a historical scenario, not "
                "a current offer."
            ),
        )
        if snapshot_id:
            self.session.add(
                ComparisonSnapshot(
                    id=snapshot_id,
                    farmer_id=farmer_id,
                    produce_batch_id=batch.id,
                    created_at=now,
                    inputs={
                        "request": data.model_dump(mode="json"),
                        "batch": {
                            "crop_id": batch.crop_id,
                            "quantity_kg": str(batch.quantity_kg),
                            "harvested_at": utc(batch.harvested_at).isoformat(),
                            "sell_by": utc(batch.sell_by).isoformat(),
                        },
                        "risk_rules": profile.rules if profile else None,
                    },
                    result=result.model_dump(mode="json"),
                )
            )
            self.session.commit()
        return result

    def snapshot(self, farmer_id: UUID, id: UUID):
        try:
            row = self.session.scalar(
                select(ComparisonSnapshot).where(
                    ComparisonSnapshot.id == id, ComparisonSnapshot.farmer_id == farmer_id
                )
            )
            if row is None:
                raise HTTPException(404)
            return ComparisonResponse.model_validate(row.result)
        except SQLAlchemyError:
            self.session.rollback()
            raise HTTPException(503) from None
