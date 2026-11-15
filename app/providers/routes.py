"""Directions adapters return distance/time only; no synthetic or straight-line routes."""

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.logistics import LogisticsInput, RouteResult


@dataclass(frozen=True)
class Point:
    latitude: float
    longitude: float


class RouteUnavailable(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class RouteProvider(Protocol):
    name: str
    cache_allowed: bool

    async def route(self, origin: Point, destination: Point, round_trip: bool) -> RouteResult: ...


class Leg(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    distance: float = Field(ge=0, le=20000000)
    duration: float = Field(ge=0, le=6000000)


class DirectionRoute(Leg):
    legs: list[Leg]


class DirectionResponse(BaseModel):
    code: str
    routes: list[DirectionRoute]


class MapboxRouteProvider:
    name = "mapbox"

    def __init__(self, client: httpx.AsyncClient, token: str, cache_allowed=False):
        self.client, self.token, self.cache_allowed = client, token, cache_allowed

    async def route(self, origin: Point, destination: Point, round_trip: bool) -> RouteResult:
        points = [origin, destination] + ([origin] if round_trip else [])
        coordinates = ";".join(f"{point.longitude},{point.latitude}" for point in points)
        try:
            response = await self.client.get(
                f"https://api.mapbox.com/directions/v5/mapbox/driving/{coordinates}",
                params={
                    "access_token": self.token,
                    "overview": "false",
                    "steps": "false",
                    "alternatives": "false",
                },
                timeout=httpx.Timeout(8.0, connect=3.0),
            )
            if response.status_code != 200:
                raise RouteUnavailable("provider_unavailable")
            payload = response.json()
            if payload.get("code") == "NoRoute":
                raise RouteUnavailable("no_route")
            data = DirectionResponse.model_validate(payload)
            if data.code != "Ok" or not data.routes:
                raise RouteUnavailable("no_route")
            legs = data.routes[0].legs
            if len(legs) != (2 if round_trip else 1):
                raise RouteUnavailable("invalid_provider_response")
            # Distances are meters; durations are seconds. Keep Decimal arithmetic downstream.
            leg = legs[0]
            return RouteResult(
                source="mapbox",
                distance_km=Decimal(str(leg.distance)) / 1000,
                duration_minutes=Decimal(str(leg.duration)) / 60,
                return_distance_km=Decimal(str(legs[1].distance)) / 1000 if round_trip else None,
                return_duration_minutes=Decimal(str(legs[1].duration)) / 60 if round_trip else None,
                attribution="Route estimate: Mapbox Directions, driving profile",
            )
        except (httpx.RequestError, ValueError, ValidationError, TypeError, AttributeError):
            # Never expose a provider exception or token-bearing request URL.
            raise RouteUnavailable("provider_unavailable") from None


class RouteCache:
    """Bounded process-local TTL cache. Providers must explicitly permit caching."""

    def __init__(self, clock=monotonic, max_entries=256):
        self.clock, self.max_entries = clock, max_entries
        self.entries = OrderedDict()

    def get(self, key):
        item = self.entries.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= self.clock():
            self.entries.pop(key, None)
            return None
        self.entries.move_to_end(key)
        return value.model_copy(update={"cached": True}, deep=True)

    def put(self, key, value, ttl):
        if ttl <= 0:
            return
        self.entries[key] = (self.clock() + ttl, value.model_copy(deep=True))
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)


def manual_route(data: LogisticsInput, reason: str | None = None) -> RouteResult:
    if data.manual_distance_km is None:
        return RouteResult(
            source="unavailable", fallback_reason=reason or "manual_distance_required"
        )
    returning = data.pricing_basis == "round_trip"
    if returning and data.manual_return_distance_km is None and not data.assume_equal_return:
        return RouteResult(source="unavailable", fallback_reason="return_distance_required")
    return RouteResult(
        source="manual",
        distance_km=data.manual_distance_km,
        duration_minutes=data.manual_duration_minutes,
        return_distance_km=(
            data.manual_distance_km if data.assume_equal_return else data.manual_return_distance_km
        )
        if returning
        else None,
        return_duration_minutes=data.manual_return_duration_minutes if returning else None,
        return_assumed_equal=returning and data.assume_equal_return,
        fallback_reason=reason,
    )
