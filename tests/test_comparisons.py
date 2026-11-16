from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.api.v1.endpoints.logistics import route_provider
from app.core.auth import Identity, verify_identity
from app.db.seed_catalog import seed_catalog
from app.models import (
    Base,
    ComparisonSnapshot,
    CropRiskProfile,
    FarmerProfile,
    LogisticsEstimate,
    Market,
    MarketPriceObservation,
    PriceImport,
    ProduceBatch,
    User,
)
from app.schemas.prices import SL
from app.services.net_return import gross_revenue, money, net_proceeds, total_cost

A, B, BATCH = UUID(int=801), UUID(int=802), UUID(int=803)
NOW = datetime.now(UTC).replace(microsecond=0)
M1, M2 = "dambulla-dec", "meegoda-dec"
HEADERS = {"Authorization": "Bearer a"}
TRANSPORT = {
    "route_mode": "manual",
    "manual_distance_km": "10",
    "manual_duration_minutes": "60",
    "tariff": {"base_charge_lkr": "100", "rate_per_km_lkr": "20"},
}
PAYLOAD = {"produce_batch_id": str(BATCH), "assume_no_loss": True, "transport": TRANSPORT}


def observation(session, market=M1, price="200", hours=1, **changes):
    row = MarketPriceObservation(
        id=uuid4(),
        natural_key=uuid4().hex,
        crop_id="tomato",
        market_id=market,
        observed_at=NOW - timedelta(hours=hours),
        observation_precision="timestamp",
        price_per_kg=Decimal(price) if price is not None else None,
        price_type="wholesale",
        quality="",
        quality_key="",
        source_name="Verified test document",
        source_key="test",
        document_reference="Synthetic integration fixture, never production data",
        source_publication_date=NOW.astimezone(SL).date(),
        imported_at=NOW - timedelta(minutes=1),
        import_id=session.scalar(select(PriceImport.id)),
        source_row_number=1,
    )
    for key, value in changes.items():
        setattr(row, key, value)
    session.add(row)
    return row


@pytest.fixture
def comparison_app(application, monkeypatch):
    Base.metadata.create_all(application.state.engine)
    with application.state.session_factory() as session:
        seed_catalog(session)
        session.add_all([User(id=id, email=f"{id}@example.test") for id in (A, B)])
        session.commit()
        session.add(
            FarmerProfile(
                user_id=A,
                full_name="Comparison farmer",
                district="Kandy",
                town="Gampola",
                preferred_language="en",
                transport_mode="hired_vehicle",
                preferred_market_ids=[],
                vehicle_capacity_kg=1000,
                estimated_transport_rate=20,
            )
        )
        session.commit()
        session.add(
            ProduceBatch(
                id=BATCH,
                farmer_id=A,
                crop_id="tomato",
                quantity_kg=100,
                harvested_at=NOW - timedelta(hours=12),
                sell_by=NOW + timedelta(hours=48),
                source_district="Kandy",
                source_town="Gampola",
                status="available",
            )
        )
        session.add(
            PriceImport(
                id=uuid4(),
                sha256="a" * 64,
                imported_by=A,
                imported_at=NOW - timedelta(minutes=1),
                filename="test.csv",
                verification_note="Synthetic test fixture",
                original_file=b"test",
                accepted=2,
                duplicates=0,
                rejected=0,
            )
        )
        session.commit()
        observation(session)
        observation(session, M2, "220")
        session.commit()

    def identity(request: Request):
        id = {"Bearer a": A, "Bearer b": B}.get(request.headers.get("authorization"))
        if id is None:
            raise HTTPException(401)
        return Identity(id, id, f"{id}@example.test")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr("app.services.market_comparison.datetime", Clock)
    application.dependency_overrides[verify_identity] = identity
    application.dependency_overrides[route_provider] = lambda: None
    return application


@pytest.fixture
def client(comparison_app):
    with TestClient(comparison_app) as client:
        yield client


def post(client, **changes):
    return client.post("/api/v1/comparisons", headers=HEADERS, json=PAYLOAD | changes)


@pytest.mark.parametrize(
    "value,expected", [("1.005", "1.01"), ("1.004", "1.00"), ("-1.005", "-1.01"), ("0", "0.00")]
)
def test_money_precision(value, expected):
    assert money(Decimal(value)) == Decimal(expected)


def test_independent_financial_formulas_and_negative_net():
    assert gross_revenue(Decimal("2.345"), Decimal("3.456789")) == Decimal("8.11")
    assert gross_revenue(Decimal(100), Decimal(0)) == 0
    assert total_cost(Decimal("100.005"), Decimal("20.005")) == Decimal("120.02")
    assert net_proceeds(Decimal("10"), Decimal("20.01")) == Decimal("-10.01")
    for fn, args in [(gross_revenue, (-1, 2)), (total_cost, (1, -2)), (net_proceeds, (-1, 2))]:
        with pytest.raises(ValueError):
            fn(*(Decimal(value) for value in args))
    with pytest.raises(ValueError):
        money(Decimal("NaN"))


def test_rank_costs_snapshots_and_deterministic_results(client, comparison_app):
    override = [
        {
            "market_id": M2,
            "transport": TRANSPORT | {"manual_distance_km": "20"},
            "additional_market_fee_lkr": "50",
        }
    ]
    first = post(client, market_overrides=override)
    assert first.status_code == 200, first.text
    data = first.json()
    assert first.headers["cache-control"] == "private, no-store"
    assert [row["market_id"] for row in data["eligible"]] == [M2, M1]
    top = data["eligible"][0]
    assert Decimal(top["expected_gross_revenue_lkr"]) == 22000
    assert Decimal(top["transport_cost_lkr"]) == 500
    assert Decimal(top["total_cost_lkr"]) == 550
    assert Decimal(top["expected_net_proceeds_lkr"]) == 21450
    assert top["quantity_basis"] == "explicit_no_loss"
    assert data["excluded"][0]["code"] == "no_compatible_price"
    second = post(client, market_overrides=override).json()
    # Cache-hit metadata can change; identical inputs/facts/time yield identical economics.
    for option in second["eligible"]:
        option["transport"]["route"]["cached"] = False
    assert first.json() | {"snapshot_id": None} == second | {"snapshot_id": None}
    saved = client.get(f"/api/v1/comparisons/{data['snapshot_id']}", headers=HEADERS)
    assert saved.json() == data
    assert (
        client.get(
            f"/api/v1/comparisons/{data['snapshot_id']}", headers={"Authorization": "Bearer b"}
        ).status_code
        == 404
    )
    with comparison_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ComparisonSnapshot)) == 2
        assert session.scalar(select(func.count()).select_from(LogisticsEstimate)) == 0


def test_grade_price_type_and_latest_not_highest(client, comparison_app):
    with comparison_app.state.session_factory() as session:
        observation(session, M1, "999", hours=2)
        observation(session, M1, "500", price_type="retail")
        observation(session, M1, "700", quality="A", quality_key="a")
        session.commit()
    result = post(client, market_ids=[M1]).json()["eligible"][0]
    assert Decimal(result["price"]["price_per_kg"]) == 200
    retail = post(client, market_ids=[M1], price_type="retail").json()["eligible"][0]
    assert Decimal(retail["price"]["price_per_kg"]) == 500
    with comparison_app.state.session_factory() as session:
        session.get(ProduceBatch, BATCH).quality = " A "
        session.commit()
    graded = post(client, market_ids=[M1]).json()["eligible"][0]
    assert graded["price"]["quality"] == "A"


@pytest.mark.parametrize(
    "kind,code",
    [("missing", "price_missing"), ("stale", "price_stale"), ("future", "price_future")],
)
def test_unusable_price(client, comparison_app, kind, code):
    with comparison_app.state.session_factory() as session:
        row = session.scalar(
            select(MarketPriceObservation).where(MarketPriceObservation.market_id == M1)
        )
        if kind == "missing":
            observation(session, M1, None, hours=0.5)
        elif kind == "stale":
            row.observed_at = NOW - timedelta(hours=73)
        else:
            row.observed_at = NOW + timedelta(minutes=1)
        session.commit()
    data = post(client, market_ids=[M1]).json()
    assert not data["eligible"] and data["excluded"][0]["code"] == code


def test_zero_price_and_freshness_at_arrival_boundary(client, comparison_app):
    with comparison_app.state.session_factory() as session:
        row = session.scalar(
            select(MarketPriceObservation).where(MarketPriceObservation.market_id == M1)
        )
        row.price_per_kg = 0
        row.observed_at = NOW - timedelta(hours=71)
        session.commit()
    result = post(client, market_ids=[M1]).json()["eligible"][0]
    assert Decimal(result["expected_gross_revenue_lkr"]) == 0
    assert Decimal(result["expected_net_proceeds_lkr"]) == -300
    assert (
        post(
            client,
            market_ids=[M1],
            market_overrides=[
                {"market_id": M1, "expected_arrival_at": (NOW + timedelta(hours=2)).isoformat()}
            ],
        ).json()["excluded"][0]["code"]
        == "price_stale_at_arrival"
    )


def test_spoilage_and_missing_rules(client, comparison_app):
    missing = post(client, market_ids=[M1], assume_no_loss=False).json()
    assert missing["excluded"][0]["code"] == "loss_assumption_missing"
    with comparison_app.state.session_factory() as session:
        session.add(
            CropRiskProfile(
                crop_id="tomato",
                version=1,
                enabled=True,
                created_by=A,
                created_at=NOW,
                rules={
                    "enabled": True,
                    "expected_selling_window_hours": 48,
                    "loss_percent_per_day": {"ambient": "24"},
                    "rationale": "Synthetic loss assumptions for tests only.",
                },
            )
        )
        session.commit()
    first = post(client, market_ids=[M1], assume_no_loss=False, storage_condition="ambient").json()[
        "eligible"
    ][0]
    later = post(
        client,
        market_ids=[M1],
        assume_no_loss=False,
        storage_condition="ambient",
        market_overrides=[
            {"market_id": M1, "expected_arrival_at": (NOW + timedelta(hours=12)).isoformat()}
        ],
    ).json()["eligible"][0]
    assert Decimal(first["expected_saleable_quantity_kg"]) == 87
    assert Decimal(later["expected_saleable_quantity_kg"]) == 76
    assert Decimal(first["expected_net_proceeds_lkr"]) > Decimal(later["expected_net_proceeds_lkr"])


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"expected_arrival_at": (NOW + timedelta(hours=48)).isoformat()}, "deadline_violated"),
        ({"expected_arrival_at": (NOW - timedelta(minutes=1)).isoformat()}, "arrival_invalid"),
        ({"transport": TRANSPORT | {"manual_duration_minutes": None}}, "arrival_unknown"),
        ({"transport": {"route_mode": "automatic"}}, "transport_cost_unknown"),
        ({"transport": TRANSPORT | {"vehicle_capacity_kg": "10"}}, "transport_validation"),
    ],
)
def test_market_exclusions(client, overrides, code):
    data = post(client, market_ids=[M1], market_overrides=[{"market_id": M1} | overrides]).json()
    assert not data["eligible"] and data["excluded"][0]["code"] == code


def test_transport_rates_quotes_and_no_double_market_charges(client):
    a = post(client, market_ids=[M1]).json()["eligible"][0]
    b = post(
        client,
        market_ids=[M1],
        transport=TRANSPORT | {"tariff": {"rate_per_km_lkr": "50", "market_handling_lkr": "20"}},
    ).json()["eligible"][0]
    assert Decimal(a["transport_cost_lkr"]) == 300
    assert Decimal(b["transport_cost_lkr"]) == 520
    quoted = post(
        client,
        market_ids=[M1],
        transport=TRANSPORT
        | {"known_quote_lkr": "700", "quote_note": "All transport and handling"},
        market_overrides=[{"market_id": M1, "additional_market_fee_lkr": "20"}],
    ).json()["eligible"][0]
    assert Decimal(quoted["total_cost_lkr"]) == 720


def test_auth_ownership_and_empty(client, comparison_app):
    assert client.post("/api/v1/comparisons", json=PAYLOAD).status_code == 401
    assert (
        client.post(
            "/api/v1/comparisons", headers={"Authorization": "Bearer b"}, json=PAYLOAD
        ).status_code
        == 404
    )
    assert post(client, produce_batch_id=str(uuid4())).status_code == 404
    assert client.get(f"/api/v1/comparisons/{uuid4()}", headers=HEADERS).status_code == 404
    assert client.get(f"/api/v1/comparisons/{uuid4()}").status_code == 401
    assert (
        post(client, market_ids=["unknown"]).json()["excluded"][0]["code"] == "market_unavailable"
    )
    with comparison_app.state.session_factory() as session:
        for market in session.scalars(select(Market)):
            market.active = False
        session.commit()
    result = post(client).json()
    assert result["eligible"] == result["excluded"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"market_ids": []},
        {"market_ids": [M1, M1]},
        {"price_type": "future"},
        {"storage_condition": "magic"},
        {"market_ids": [M1], "market_overrides": [{"market_id": M2}]},
        {"market_overrides": [{"market_id": M1}, {"market_id": M1}]},
        {"transport": {"known_quote_lkr": -1}},
        {"transport": {"known_quote_lkr": 1}},
        {"market_overrides": [{"market_id": M1, "expected_arrival_at": "2026-11-16T01:00:00"}]},
        {"market_overrides": [{"market_id": M1, "additional_market_fee_lkr": "NaN"}]},
        {"quantity_kg": 1},
        {"assessed_at": NOW.isoformat()},
    ],
)
def test_validation(client, changes):
    assert post(client, **changes).status_code == 422


def test_database_error_sanitized(client, monkeypatch):
    def fail(*args, **kwargs):
        raise OperationalError("private", {}, Exception("secret"))

    monkeypatch.setattr("app.services.market_comparison.ProduceRepository.get", fail)
    response = post(client)
    assert response.status_code == 503 and "secret" not in response.text


def test_routing_failure_and_provider_retention(client, comparison_app):
    from app.providers.routes import RouteUnavailable
    from app.schemas.logistics import RouteResult

    class Provider:
        name = "mapbox"
        cache_allowed = False
        fail = True

        async def route(self, *args):
            if self.fail:
                raise RouteUnavailable("provider_unavailable")
            return RouteResult(
                source="mapbox", distance_km=Decimal(10), duration_minutes=Decimal(60)
            )

    provider = Provider()
    comparison_app.dependency_overrides[route_provider] = lambda: provider
    with comparison_app.state.session_factory() as session:
        batch = session.get(ProduceBatch, BATCH)
        batch.source_latitude, batch.source_longitude = 7, 80
        market = session.get(Market, M1)
        market.latitude, market.longitude = 8, 81
        market.coordinate_source = "https://example.test/test-coordinate"
        market.coordinates_verified_on = NOW.date()
        session.commit()
    failed = post(client, market_ids=[M1], transport={"route_mode": "automatic"}).json()
    assert failed["excluded"][0]["code"] == "transport_cost_unknown"
    fallback = post(
        client, market_ids=[M1], transport=TRANSPORT | {"route_mode": "automatic"}
    ).json()
    assert fallback["eligible"][0]["transport"]["route"]["source"] == "manual"
    provider.fail = False
    with comparison_app.state.session_factory() as session:
        count = session.scalar(select(func.count()).select_from(ComparisonSnapshot))
    live = post(client, market_ids=[M1], transport={"route_mode": "automatic"}).json()
    assert live["snapshot_id"] is None
    assert live["eligible"][0]["transport"]["route"]["source"] == "mapbox"
    with comparison_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ComparisonSnapshot)) == count


def test_source_selection_missing_report_and_ties(client, comparison_app):
    with comparison_app.state.session_factory() as session:
        observation(session, M1, "1", hours=0.5, source_key="another")
        observation(session, M1, None, hours=0.25)
        session.commit()
    data = post(client, market_ids=[M1]).json()
    assert Decimal(data["eligible"][0]["price"]["price_per_kg"]) == 1
    with comparison_app.state.session_factory() as session:
        for row in session.scalars(select(MarketPriceObservation)):
            row.price_per_kg = 220
        session.commit()
    tied = post(client, market_ids=[M2, M1]).json()
    assert [row["market_id"] for row in tied["eligible"]] == sorted([M1, M2])


@pytest.mark.parametrize("state", ["sold", "archived", "expired"])
def test_closed_deadline_and_inactive_market(client, comparison_app, state):
    with comparison_app.state.session_factory() as session:
        batch = session.get(ProduceBatch, BATCH)
        batch.status = state
        if state == "archived":
            batch.archived_at = NOW
        elif state == "expired":
            batch.sell_by = NOW - timedelta(seconds=1)
        session.commit()
    data = post(client, market_ids=[M1]).json()
    assert not data["eligible"] and data["excluded"][0]["code"] == "batch_not_sellable"
