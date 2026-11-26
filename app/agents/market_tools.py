"Allowlisted typed adapters; no tool can sell produce, pay or arrange transport."

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.models import CropRiskProfile, Market
from app.repositories.prices import PriceRepository
from app.repositories.produce import ProduceRepository
from app.schemas.comparisons import ComparisonResponse
from app.schemas.crop_risk import RiskResponse, RiskRules
from app.schemas.logistics import LogisticsInput, LogisticsResponse
from app.schemas.prices import PricePage, key_text
from app.schemas.recommendations import AgentConstraints
from app.services.crop_risk import RiskTimeError, evaluate_risk
from app.services.prices import PriceService
from app.services.transport_cost import LogisticsValidationError


class InputNeeded(ValueError):
    pass


class VerifiedMarketTools:
    def __init__(self, session, farmer_id: UUID, comparison):
        self.session, self.farmer_id, self.comparison = session, farmer_id, comparison

    def batch(self, data: AgentConstraints):
        batch = ProduceRepository(self.session).get(self.farmer_id, data.produce_batch_id)
        if batch is None:
            raise HTTPException(404)
        return batch

    def market_ids(self, data: AgentConstraints) -> list[str]:
        return data.market_ids or list(
            self.session.scalars(
                select(Market.id).where(Market.active.is_(True)).order_by(Market.id)
            ).all()
        )

    async def market_context(self, data: AgentConstraints) -> list[str]:
        self.batch(data)
        ids = self.market_ids(data)
        if not ids:
            raise InputNeeded(
                "No active markets are configured. Ask an administrator to review the catalog."
            )
        if len(ids) > 5:
            raise InputNeeded(
                "Choose at most five candidate markets to keep this agent run bounded."
            )
        markets = self.session.scalars(select(Market).where(Market.id.in_(ids))).all()
        if len(markets) != len(ids) or any(not market.active for market in markets):
            raise InputNeeded("Choose active markets from the verified catalog.")
        return ids

    async def price_observations(self, data: AgentConstraints) -> PricePage:
        batch = self.batch(data)
        return PriceService(PriceRepository(self.session), self.comparison.max_age).latest(
            {
                "crop_id": batch.crop_id,
                "price_type": data.price_type,
                "quality_key": key_text(batch.quality or ""),
            },
            page=1,
            page_size=100,
        )

    async def transport_estimates(self, data: AgentConstraints) -> list[LogisticsResponse]:
        overrides = {row.market_id: row for row in data.market_overrides}
        output = []
        for id in self.market_ids(data):
            override = overrides.get(id)
            options = override.transport if override and override.transport else data.transport
            try:
                result = await self.comparison.logistics.estimate(
                    self.farmer_id,
                    LogisticsInput(
                        **options.model_dump(), produce_batch_id=data.produce_batch_id, market_id=id
                    ),
                    persist=False,
                )
            except LogisticsValidationError as exc:
                raise InputNeeded(exc.message) from None
            except HTTPException as exc:
                if exc.status_code in {404, 409, 422}:
                    raise InputNeeded(
                        "Review the selected markets, batch status and transport settings."
                    ) from None
                raise
            if result.route.source == "mapbox":
                # Never put provider output/derived cost in graph checkpoints or prompts.
                raise InputNeeded(
                    "For a durable agent result, enter a known whole-batch quote "
                    "and final arrival, or a manual distance and duration. "
                    "Mapbox-derived results cannot be saved."
                )
            output.append(result)
        return output

    async def crop_risk(
        self, data: AgentConstraints, transport: list[LogisticsResponse]
    ) -> list[RiskResponse]:
        batch = self.batch(data)
        row = self.session.scalar(
            select(CropRiskProfile)
            .where(CropRiskProfile.crop_id == batch.crop_id)
            .order_by(CropRiskProfile.version.desc())
            .limit(1)
        )
        rules = RiskRules.model_validate(row.rules) if row else None
        overrides = {row.market_id: row for row in data.market_overrides}
        now = datetime.now(UTC)
        output = []
        for estimate in transport:
            override = overrides.get(estimate.market_id)
            arrival = (
                override.expected_arrival_at if override and override.expected_arrival_at else None
            )
            if (
                arrival is None
                and estimate.trips == 1
                and estimate.route.duration_minutes is not None
            ):
                arrival = now + timedelta(minutes=float(estimate.route.duration_minutes))
            if arrival is None:
                continue  # Comparison returns precise per-market missing-arrival reasons.
            try:
                output.append(
                    evaluate_risk(
                        batch_id=batch.id,
                        crop_id=batch.crop_id,
                        quantity=batch.quantity_kg,
                        harvested_at=batch.harvested_at,
                        deadline=batch.sell_by,
                        assessment_at=now,
                        arrival_at=arrival,
                        storage=data.storage_condition,
                        rules=rules,
                        profile_version=row.version if row else None,
                    )
                )
            except (RiskTimeError, OverflowError, ValueError):
                continue  # Final comparison remains authoritative, including invalid arrivals.
        return output

    async def market_comparison(self, data: AgentConstraints) -> ComparisonResponse:
        # The final calculator revalidates all observations/constraints; preliminary tool data
        # cannot override its prices, ranking or figures.
        return await self.comparison.compare(self.farmer_id, data, persist=False)
