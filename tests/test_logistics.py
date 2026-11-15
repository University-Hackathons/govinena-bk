from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.api.v1.endpoints.logistics import route_provider
from app.core.auth import Identity, verify_identity
from app.db.seed_catalog import seed_catalog
from app.models import Base, FarmerProfile, LogisticsEstimate, Market, ProduceBatch, User
from app.providers.routes import MapboxRouteProvider, Point, RouteCache, RouteUnavailable
from app.repositories.logistics import LogisticsRepository
from app.schemas.logistics import RouteResult, Tariff
from app.services.transport_cost import calculate_cost, loads

A, B = UUID(int=601), UUID(int=602)
BATCH = UUID(int=603)
PAYLOAD = {
    "produce_batch_id": str(BATCH),
    "market_id": "dambulla-dec",
    "route_mode": "manual",
    "manual_distance_km": "10",
    "tariff": {
        "base_charge_lkr": "300",
        "rate_per_km_lkr": "20",
        "loading_lkr": "50",
        "unloading_lkr": "40",
        "toll_lkr": "10",
        "market_handling_lkr": "5",
    },
}


def headers(user="a"):
    return {"Authorization": f"Bearer {user}"}


@pytest.fixture
def logistics_app(application):
    Base.metadata.create_all(application.state.engine)
    with application.state.session_factory() as session:
        seed_catalog(session)
        session.add_all([User(id=A, email="a@example.test"), User(id=B, email="b@example.test")])
        session.commit()
        for id in [A, B]:
            session.add(
                FarmerProfile(
                    user_id=id,
                    full_name="Logistics Farmer",
                    district="Kandy",
                    town="Gampola",
                    preferred_language="en",
                    transport_mode="hired_vehicle",
                    vehicle_capacity_kg=1000,
                    estimated_transport_rate=20,
                    latitude=7.164,
                    longitude=80.569,
                    preferred_market_ids=[],
                )
            )
        session.commit()
        session.add(
            ProduceBatch(
                id=BATCH,
                farmer_id=A,
                crop_id="tomato",
                quantity_kg=100,
                harvested_at=datetime.now(UTC) - timedelta(hours=1),
                sell_by=datetime.now(UTC) + timedelta(days=2),
                source_district="Kandy",
                source_town="Gampola",
                source_latitude=7.164,
                source_longitude=80.569,
                status="available",
            )
        )
        session.commit()

    def identity(request: Request):
        id = {"Bearer a": A, "Bearer b": B}.get(request.headers.get("authorization"))
        if id is None:
            raise HTTPException(401)
        return Identity(id, id, f"{id}@example.test")

    application.dependency_overrides[verify_identity] = identity
    application.dependency_overrides[route_provider] = lambda: None
    return application


@pytest.fixture
def logistics_client(logistics_app):
    with TestClient(logistics_app) as client:
        yield client


def post(client, **changes):
    return client.post("/api/v1/logistics/estimate", headers=headers(), json=PAYLOAD | changes)


def test_known_distance_breakdown_and_persistence(logistics_client, logistics_app):
    response = post(logistics_client)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "complete" and data["cost_source"] == "calculated"
    assert Decimal(data["effective_cost_lkr"]) == 605
    assert Decimal(data["breakdown"]["distance_charge_lkr"]) == 200
    assert data["route"]["source"] == "manual" and data["route"]["duration_minutes"] is None
    assert data["trips"] == 1
    assert response.headers["cache-control"] == "private, no-store"
    history = logistics_client.get(
        f"/api/v1/logistics/estimates?batch_id={BATCH}", headers=headers()
    ).json()["items"]
    assert history[0]["id"] == data["estimate_id"]
    assert history[0]["assumptions"]["formula_version"] == "transport_v1"
    with logistics_app.state.session_factory() as session:
        record = session.get(LogisticsEstimate, UUID(data["estimate_id"]))
        assert record.assumptions["quantity_kg"] == "100.000"
        assert record.recorded_result["route"]["source"] == "manual"


def test_one_way_round_trip_and_asymmetric_return(logistics_client):
    one = post(logistics_client).json()
    round_trip = post(
        logistics_client, pricing_basis="round_trip", manual_return_distance_km="15"
    ).json()
    equal = post(logistics_client, pricing_basis="round_trip", assume_equal_return=True).json()
    assert Decimal(one["effective_cost_lkr"]) == 605
    assert Decimal(round_trip["effective_cost_lkr"]) == 905
    assert Decimal(equal["effective_cost_lkr"]) == 805
    assert equal["route"]["return_assumed_equal"] is True
    assert Decimal(round_trip["breakdown"]["base_charge_lkr"]) == 300


def test_capacity_rejection_and_explicit_multiple_deliveries(logistics_client):
    too_small = post(logistics_client, vehicle_capacity_kg="40")
    assert too_small.status_code == 422 and "capacity" in too_small.json()["error"]["message"]
    data = post(logistics_client, vehicle_capacity_kg="40", allow_multiple_trips=True).json()
    assert data["trips"] == 3
    assert [Decimal(value) for value in data["trip_loads_kg"]] == [40, 40, 20]
    assert Decimal(data["effective_cost_lkr"]) == 1815
    assert Decimal(data["breakdown"]["billable_distance_km"]) == 30
    assert (
        post(logistics_client, vehicle_capacity_kg="0.001", allow_multiple_trips=True).status_code
        == 422
    )


def test_quote_override_is_whole_batch_and_can_work_without_route(logistics_client):
    data = post(
        logistics_client,
        known_quote_lkr="700",
        quote_note="Driver quote for all deliveries",
        vehicle_capacity_kg="40",
        allow_multiple_trips=True,
    ).json()
    assert data["cost_source"] == "user_quote" and Decimal(data["effective_cost_lkr"]) == 700
    assert Decimal(data["breakdown"]["calculated_total_lkr"]) == 1815
    unavailable = post(
        logistics_client,
        route_mode="automatic",
        manual_distance_km=None,
        tariff={"rate_per_km_lkr": None},
        known_quote_lkr="0",
        quote_note="User reports no transport charge",
    ).json()
    assert unavailable["status"] == "complete" and unavailable["route"]["source"] == "unavailable"
    assert unavailable["breakdown"] is None and Decimal(unavailable["effective_cost_lkr"]) == 0


def test_missing_location_no_route_no_fake_zero(logistics_client, logistics_app):
    data = post(logistics_client, route_mode="automatic", manual_distance_km=None).json()
    assert data["status"] == "needs_input" and data["effective_cost_lkr"] is None
    assert data["route"]["fallback_reason"] == "unverified_market_location"
    assert data["estimate_id"] is None
    with logistics_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(LogisticsEstimate)) == 0
        batch = session.get(ProduceBatch, BATCH)
        batch.source_latitude = batch.source_longitude = None
        session.commit()
    missing = post(logistics_client, route_mode="automatic", manual_distance_km=None).json()
    assert missing["route"]["fallback_reason"] == "missing_origin"
    fallback = post(logistics_client, route_mode="automatic").json()
    assert fallback["route"]["source"] == "manual" and fallback["status"] == "complete"


def test_missing_rate_and_capacity_have_clear_behavior(logistics_client, logistics_app):
    with logistics_app.state.session_factory() as session:
        profile = session.get(FarmerProfile, A)
        profile.estimated_transport_rate = None
        session.commit()
    data = post(logistics_client, tariff={}).json()
    assert data["status"] == "needs_input" and data["breakdown"] is None
    with logistics_app.state.session_factory() as session:
        session.get(FarmerProfile, A).vehicle_capacity_kg = None
        session.commit()
    assert post(logistics_client).status_code == 422


@pytest.mark.parametrize(
    "bad",
    [
        {"vehicle_capacity_kg": -1},
        {"vehicle_capacity_kg": 0},
        {"manual_distance_km": -1},
        {"manual_distance_km": "NaN"},
        {"manual_duration_minutes": -1},
        {"known_quote_lkr": -1},
        {"known_quote_lkr": 100, "quote_note": ""},
        {"known_quote_lkr": "Infinity"},
        {"tariff": {"rate_per_km_lkr": -1}},
        {"tariff": {"base_charge_lkr": "1.001"}},
        {"tariff": {"loading_lkr": -1}},
        {"pricing_basis": "bad"},
        {"route_mode": "bad"},
        {"manual_return_distance_km": 10},
        {"farmer_id": str(B)},
        {"quantity_kg": 5},
    ],
)
def test_invalid_input(logistics_client, bad):
    assert post(logistics_client, **bad).status_code == 422


def test_owner_authentication_and_archived_batch(logistics_client, logistics_app):
    assert logistics_client.post("/api/v1/logistics/estimate", json=PAYLOAD).status_code == 401
    assert (
        logistics_client.post(
            "/api/v1/logistics/estimate", headers=headers("b"), json=PAYLOAD
        ).status_code
        == 404
    )
    assert (
        logistics_client.get(
            f"/api/v1/logistics/estimates?batch_id={BATCH}", headers=headers("b")
        ).status_code
        == 404
    )
    assert logistics_client.get(f"/api/v1/logistics/estimates?batch_id={BATCH}").status_code == 401
    assert post(logistics_client, market_id="unknown").status_code == 404
    with logistics_app.state.session_factory() as session:
        row = session.get(ProduceBatch, BATCH)
        row.status, row.archived_at = "archived", datetime.now(UTC)
        session.commit()
    assert post(logistics_client).status_code == 409


class FakeProvider:
    name = "mapbox"
    cache_allowed = True
    calls = 0
    fail = False

    async def route(self, origin, destination, round_trip):
        self.calls += 1
        if self.fail:
            raise RouteUnavailable("provider_unavailable")
        return RouteResult(
            source="mapbox",
            distance_km=Decimal("100"),
            duration_minutes=Decimal("60"),
            return_distance_km=Decimal("110") if round_trip else None,
            return_duration_minutes=Decimal("65") if round_trip else None,
        )


def enable_provider(app, provider):
    with app.state.session_factory() as session:
        market = session.get(Market, "dambulla-dec")
        market.latitude, market.longitude = 7.8, 80.6
        market.coordinate_source = "https://example.test/verified-test-location"
        market.coordinates_verified_on = date.today()
        session.commit()
    app.dependency_overrides[route_provider] = lambda: provider


def test_api_routes_cache_success_and_never_persist_provider_results(
    logistics_client, logistics_app
):
    provider = FakeProvider()
    enable_provider(logistics_app, provider)
    first = post(
        logistics_client,
        route_mode="automatic",
        pricing_basis="round_trip",
        manual_distance_km=None,
    ).json()
    assert Decimal(first["breakdown"]["billable_distance_km"]) == 210
    assert first["route"]["cached"] is False
    second = post(
        logistics_client,
        route_mode="automatic",
        pricing_basis="round_trip",
        manual_distance_km=None,
    ).json()
    assert provider.calls == 1 and second["route"]["cached"] is True
    with logistics_app.state.session_factory() as session:
        record = session.get(LogisticsEstimate, UUID(first["estimate_id"]))
        assert record.recorded_result is None
        assert record.assumptions["route_results_retained"] is False
        assert "distance_km" not in record.assumptions
    provider.cache_allowed = False
    post(logistics_client, route_mode="automatic", manual_distance_km=None)
    post(logistics_client, route_mode="automatic", manual_distance_km=None)
    assert provider.calls == 3


def test_provider_failure_manual_fallback_or_quote(logistics_client, logistics_app):
    provider = FakeProvider()
    provider.fail = True
    enable_provider(logistics_app, provider)
    data = post(logistics_client, route_mode="automatic").json()
    assert data["route"]["source"] == "manual"
    assert data["route"]["fallback_reason"] == "provider_unavailable"
    no_distance = post(logistics_client, route_mode="automatic", manual_distance_km=None).json()
    assert no_distance["status"] == "needs_input" and no_distance["route"]["distance_km"] is None


def test_route_cache_ttl_bounded_and_clones_values():
    clock = [0]
    cache = RouteCache(clock=lambda: clock[0], max_entries=1)
    value = RouteResult(source="manual", distance_km=Decimal(10))
    cache.put("a", value, 5)
    assert cache.get("a").cached is True and value.cached is False
    clock[0] = 6
    assert cache.get("a") is None
    cache.put("b", value, 5)
    cache.put("c", value, 5)
    assert cache.get("b") is None


async def test_mapbox_adapter_coordinate_order_units_and_return_leg():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "routes": [
                    {
                        "distance": 25000,
                        "duration": 2100,
                        "legs": [
                            {"distance": 10000, "duration": 900},
                            {"distance": 15000, "duration": 1200},
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = MapboxRouteProvider(client, "test-token")
        result = await provider.route(Point(7, 80), Point(8, 81), True)
        assert result.distance_km == 10 and result.return_distance_km == 15
        assert result.duration_minutes == 15 and result.return_duration_minutes == 20
        assert "/80,7;81,8;80,7" in str(requests[0].url)
        assert provider.cache_allowed is False


@pytest.mark.parametrize(
    "status,payload",
    [
        (503, {}),
        (200, {"code": "NoRoute", "routes": []}),
        (200, {"code": "Ok", "routes": [{"distance": 10, "duration": 1, "legs": []}]}),
    ],
)
async def test_mapbox_failures_are_safe(status, payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    ) as client:
        with pytest.raises(RouteUnavailable) as error:
            await MapboxRouteProvider(client, "private-token").route(
                Point(7, 80), Point(8, 81), False
            )
        assert "private-token" not in str(error.value)


def test_known_calculation_rounding_and_zero_distance():
    route = RouteResult(source="manual", distance_km=Decimal("0"))
    result = calculate_cost(route, Tariff(base_charge_lkr=100, rate_per_km_lkr=20), "one_way", 1)
    assert result.calculated_total_lkr == 100
    assert loads(Decimal("100"), Decimal("100"), False)[0] == 1
    route.distance_km = Decimal("1.111")
    result = calculate_cost(route, Tariff(rate_per_km_lkr=Decimal("1.25")), "one_way", 1)
    assert result.calculated_total_lkr == Decimal("1.39")


def test_storage_failure_is_safe(logistics_client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OperationalError("private connection", {}, Exception("secret"))

    monkeypatch.setattr(LogisticsRepository, "save", unavailable)
    response = post(logistics_client)
    assert response.status_code == 503 and "secret" not in response.text


async def test_route_timeout_is_a_safe_fallback():
    def fail(request):
        raise httpx.ReadTimeout("private-token in provider request", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(RouteUnavailable) as error:
            await MapboxRouteProvider(client, "private-token").route(
                Point(7, 80), Point(8, 81), False
            )
        assert error.value.reason == "provider_unavailable"
        assert "private-token" not in str(error.value)


def test_equal_return_distance_can_have_a_separate_known_duration(logistics_client):
    response = post(
        logistics_client,
        pricing_basis="round_trip",
        assume_equal_return=True,
        manual_return_duration_minutes="45",
    )
    assert response.status_code == 200
    assert Decimal(response.json()["route"]["return_duration_minutes"]) == 45
    assert Decimal(response.json()["route"]["return_distance_km"]) == 10
