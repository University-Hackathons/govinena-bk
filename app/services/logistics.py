import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.models.catalog import Market
from app.models.farmer import FarmerProfile
from app.models.logistics import LogisticsEstimate
from app.providers.routes import Point, RouteCache, RouteProvider, RouteUnavailable, manual_route
from app.repositories.logistics import LogisticsRepository
from app.schemas.logistics import EstimateHistory, LogisticsInput, LogisticsResponse, StoredEstimate
from app.schemas.produce import utc
from app.services.transport_cost import calculate_cost, loads

FALLBACK_MESSAGES = {
    "missing_origin": (
        "This batch has no source pin. Enter a known road distance or a whole-batch quote."
    ),
    "unverified_market_location": (
        "The market has no verified coordinates. Enter a known road distance "
        "or a whole-batch quote."
    ),
    "provider_not_configured": (
        "Automatic routing is not configured. Use a known road distance or a whole-batch quote."
    ),
    "provider_unavailable": (
        "The routing service could not be reached or validated. Use a known "
        "road distance or quote, or try again later."
    ),
    "invalid_provider_response": (
        "The routing response was incomplete. Use a known road distance or quote."
    ),
    "no_route": (
        "No driving route was found. Enter a known road distance or quote if transport is possible."
    ),
    "manual_distance_required": (
        "Enter a known road distance to calculate transport, or supply a whole-batch quote."
    ),
    "return_distance_required": (
        "Enter the return distance or explicitly assume it equals the outbound distance."
    ),
}


class LogisticsService:
    def __init__(
        self,
        repository: LogisticsRepository,
        provider: RouteProvider | None,
        cache: RouteCache,
        ttl=300,
    ):
        self.repo, self.session, self.provider, self.cache, self.ttl = (
            repository,
            repository.session,
            provider,
            cache,
            ttl,
        )

    def _manual(self, data, reason=None):
        fields = {
            name: value
            for name, value in data.model_dump(mode="json").items()
            if name.startswith("manual_") or name in ("pricing_basis", "assume_equal_return")
        }
        key = ("manual", json.dumps(fields, sort_keys=True), reason)
        cached = self.cache.get(key) if self.ttl else None
        if cached:
            return cached
        result = manual_route(data, reason)
        if result.source == "manual":
            self.cache.put(key, result, self.ttl)
        return result

    async def resolve_route(self, data, batch, market):
        if data.route_mode == "manual":
            return self._manual(data)
        reason = None
        if batch.source_latitude is None or batch.source_longitude is None:
            reason = "missing_origin"
        elif (
            market.latitude is None
            or market.longitude is None
            or not market.coordinate_source
            or not market.coordinates_verified_on
        ):
            reason = "unverified_market_location"
        elif self.provider is None:
            reason = "provider_not_configured"
        if reason:
            return self._manual(data, reason)
        origin = Point(batch.source_latitude, batch.source_longitude)
        destination = Point(market.latitude, market.longitude)
        key = (self.provider.name, origin, destination, data.pricing_basis)
        can_cache = self.provider.cache_allowed and self.ttl > 0
        if can_cache:
            cached = self.cache.get(key)
            if cached:
                return cached
        try:
            result = await self.provider.route(
                origin, destination, data.pricing_basis == "round_trip"
            )
            if can_cache:
                self.cache.put(key, result, self.ttl)
            return result
        except RouteUnavailable as exc:
            return self._manual(data, exc.reason)

    async def estimate(self, farmer_id: UUID, data: LogisticsInput):
        try:
            batch = self.repo.batch(farmer_id, data.produce_batch_id)
            if batch is None:
                raise HTTPException(404)
            if batch.status == "archived":
                raise HTTPException(409)
            profile = self.session.get(FarmerProfile, farmer_id)
            if profile is None:
                raise HTTPException(409)
            market = self.session.get(Market, data.market_id)
            if market is None:
                raise HTTPException(404)
            if not market.active:
                raise HTTPException(422)
            capacity = (
                data.vehicle_capacity_kg
                if data.vehicle_capacity_kg is not None
                else profile.vehicle_capacity_kg
            )
            trips, trip_loads = loads(batch.quantity_kg, capacity, data.allow_multiple_trips)
            rate = (
                data.tariff.rate_per_km_lkr
                if data.tariff.rate_per_km_lkr is not None
                else profile.estimated_transport_rate
            )
            tariff = data.tariff.model_copy(update={"rate_per_km_lkr": rate})
            route = await self.resolve_route(data, batch, market)
            breakdown = calculate_cost(route, tariff, data.pricing_basis, trips)
            quote = data.known_quote_lkr
            effective = (
                quote
                if quote is not None
                else breakdown.calculated_total_lkr
                if breakdown
                else None
            )
            cost_source = (
                "user_quote" if quote is not None else "calculated" if breakdown else "unavailable"
            )
            messages = []
            if route.fallback_reason:
                messages.append(
                    FALLBACK_MESSAGES.get(route.fallback_reason, "Use a manual distance or quote.")
                )
            if rate is None and quote is None:
                messages.append(
                    "Enter a rate per km or a known whole-batch quote; no default market "
                    "fare is assumed."
                )
            if trips > 1:
                messages.append(
                    f"This load needs {trips} deliveries. Per-delivery fees and "
                    f"billable distance are multiplied by {trips}."
                )
            if route.return_assumed_equal:
                messages.append(
                    "The manual return distance is assumed equal to outbound, as "
                    "explicitly selected."
                )
            if quote is not None:
                messages.append(
                    "The known quote replaces the calculated total and covers the entire "
                    "batch, including every trip."
                )
            messages.append(
                "Flat charges apply once per delivery. Toll/handling entries must "
                "cover the selected billing basis. Waiting time and repositioning "
                "duration are not inferred."
            )
            assumptions = {
                "formula_version": "transport_v1",
                "rounding": "ROUND_HALF_UP to 2 decimals per component after trip multiplication",
                "request": data.model_dump(mode="json"),
                "quantity_kg": str(batch.quantity_kg),
                "vehicle_capacity_kg": str(capacity),
                "trips": trips,
                "trip_loads_kg": [str(value) for value in trip_loads],
                "transport_mode": data.transport_mode or profile.transport_mode,
                "resolved_tariff": tariff.model_dump(mode="json"),
                "pricing_basis": data.pricing_basis,
                "capacity_source": "user_input"
                if data.vehicle_capacity_kg is not None
                else "farmer_profile",
                "rate_source": "user_input"
                if data.tariff.rate_per_km_lkr is not None
                else "farmer_profile"
                if rate is not None
                else "unknown",
                "source_location": {
                    "district": batch.source_district,
                    "town": batch.source_town,
                    "latitude": batch.source_latitude,
                    "longitude": batch.source_longitude,
                },
                "market_location": {
                    "market_id": market.id,
                    "latitude": market.latitude,
                    "longitude": market.longitude,
                    "coordinate_source": market.coordinate_source,
                },
                "route_source": route.source,
                "route_results_retained": route.source != "mapbox",
                "routing_profile": "mapbox/driving" if route.source == "mapbox" else None,
                "charges_scope": "per_delivery",
                "quote_scope": "whole_batch_all_trips",
            }
            record_id = None
            if effective is not None:
                # Keep user inputs, but no Mapbox route output or derived cost in durable history.
                recorded = None
                if route.source != "mapbox":
                    recorded = {
                        "route": route.model_dump(mode="json"),
                        "breakdown": breakdown.model_dump(mode="json") if breakdown else None,
                        "effective_cost_lkr": str(effective),
                        "cost_source": cost_source,
                    }
                elif quote is not None:
                    recorded = {"effective_cost_lkr": str(quote), "cost_source": "user_quote"}
                record = LogisticsEstimate(
                    farmer_id=farmer_id,
                    produce_batch_id=batch.id,
                    market_id=market.id,
                    market_name=market.name,
                    created_at=datetime.now(UTC),
                    assumptions=assumptions,
                    recorded_result=recorded,
                )
                self.repo.save(record)
                record_id = record.id
            return LogisticsResponse(
                estimate_id=record_id,
                status="complete" if effective is not None else "needs_input",
                produce_batch_id=batch.id,
                market_id=market.id,
                market_name=market.name,
                quantity_kg=batch.quantity_kg,
                vehicle_capacity_kg=capacity,
                trips=trips,
                trip_loads_kg=trip_loads,
                route=route,
                breakdown=breakdown,
                effective_cost_lkr=effective,
                cost_source=cost_source,
                assumptions=assumptions,
                messages=messages,
            )
        except SQLAlchemyError:
            self.session.rollback()
            raise HTTPException(503) from None

    def history(self, farmer_id: UUID, batch_id: UUID):
        try:
            if self.repo.batch(farmer_id, batch_id) is None:
                raise HTTPException(404)
            rows = self.repo.history(farmer_id, batch_id)
            return EstimateHistory(
                items=[
                    StoredEstimate(
                        id=row.id,
                        produce_batch_id=row.produce_batch_id,
                        market_id=row.market_id,
                        market_name=row.market_name,
                        created_at=utc(row.created_at),
                        assumptions=row.assumptions,
                        recorded_result=row.recorded_result,
                    )
                    for row in rows
                ]
            )
        except SQLAlchemyError:
            self.session.rollback()
            raise HTTPException(503) from None
