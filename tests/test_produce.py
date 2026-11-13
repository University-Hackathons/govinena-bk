from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.auth import Identity, verify_identity
from app.db.seed_catalog import seed_catalog
from app.models import Base, Crop, FarmerProfile, ProduceBatch, User
from app.repositories.produce import ProduceRepository
from app.schemas.produce import ProduceCreate
from app.services.produce import ProduceService

A, B = UUID(int=101), UUID(int=102)
NOW = datetime.now(UTC)
PAYLOAD = {
    "crop_id": "tomato",
    "quantity_kg": "120.125",
    "quality": "Farmer-described quality",
    "harvested_at": (NOW - timedelta(hours=2)).isoformat(),
    "sell_by": (NOW + timedelta(days=2)).isoformat(),
    "notes": "Test batch",
}


def headers(user="a"):
    return {"Authorization": f"Bearer user-{user}"}


@pytest.fixture
def produce_app(application):
    Base.metadata.create_all(application.state.engine)
    with application.state.session_factory() as session:
        seed_catalog(session)
        session.add_all([User(id=A, email="a@example.test"), User(id=B, email="b@example.test")])
        session.commit()
        for user in [A, B]:
            session.add(
                FarmerProfile(
                    user_id=user,
                    full_name="Test Farmer",
                    district="Kandy",
                    town="Gampola",
                    preferred_language="en",
                    transport_mode="other",
                    latitude=7.164,
                    longitude=80.569,
                    preferred_market_ids=[],
                )
            )
        session.commit()

    def identity(request: Request):
        id = {"Bearer user-a": A, "Bearer user-b": B}.get(request.headers.get("authorization"))
        if id is None:
            raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
        return Identity(id, id, f"{id}@example.test")

    application.dependency_overrides[verify_identity] = identity
    return application


@pytest.fixture
def produce_client(produce_app):
    with TestClient(produce_app) as client:
        yield client


def create(client, **values):
    response = client.post("/api/v1/produce", headers=headers(), json=PAYLOAD | values)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_create_read_edit_refresh_and_location_snapshot(produce_client, produce_app):
    empty = produce_client.get("/api/v1/produce", headers=headers()).json()
    assert empty == {"items": [], "total": 0, "page": 1, "page_size": 12}
    batch = create(produce_client)
    id = batch["id"]
    assert batch["farmer_id"] == str(A)
    assert batch["crop"]["name_en"] == "Tomato"
    assert Decimal(batch["quantity_kg"]) == Decimal("120.125")
    assert batch["source_location"] == {
        "district": "Kandy",
        "town": "Gampola",
        "latitude": 7.164,
        "longitude": 80.569,
    }
    assert batch["status"] == "available" and batch["archived_at"] is None
    response = produce_client.patch(
        f"/api/v1/produce/{id}",
        headers=headers(),
        json={
            "quantity_kg": "85.500",
            "crop_id": "carrot",
            "notes": None,
            "quality": None,
            "status": "reserved",
            "source_location": {"district": "Matale", "town": "Dambulla"},
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["crop_id"] == "carrot" and updated["crop"]["name_en"] == "Carrot"
    assert updated["notes"] is None and updated["quality"] is None
    assert updated["status"] == "reserved"
    assert updated["source_location"]["latitude"] is None
    assert produce_client.get(f"/api/v1/produce/{id}", headers=headers()).json() == updated
    with produce_app.state.session_factory() as session:
        session.get(FarmerProfile, A).town = "Changed profile town"
        session.commit()
    assert (
        produce_client.get(f"/api/v1/produce/{id}", headers=headers()).json()["source_location"][
            "town"
        ]
        == "Dambulla"
    )
    reset = produce_client.patch(
        f"/api/v1/produce/{id}", headers=headers(), json={"source_location": None}
    )
    assert reset.json()["source_location"]["town"] == "Changed profile town"
    with produce_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProduceBatch)) == 1
        assert session.get(ProduceBatch, UUID(id)).quantity_kg == Decimal("85.500")


def test_delete_archives_idempotently_and_retains_readable_history(produce_client, produce_app):
    batch = create(produce_client)
    url = f"/api/v1/produce/{batch['id']}"
    for _ in range(2):
        response = produce_client.delete(url, headers=headers())
        assert response.status_code == 204 and not response.content
    assert produce_client.get("/api/v1/produce", headers=headers()).json()["total"] == 0
    archived = produce_client.get(url, headers=headers()).json()
    assert archived["status"] == "archived" and archived["archived_at"]
    assert (
        produce_client.get("/api/v1/produce?status=archived", headers=headers()).json()["total"]
        == 1
    )
    assert (
        produce_client.patch(url, headers=headers(), json={"notes": "forbidden"}).status_code == 409
    )
    with produce_app.state.session_factory() as session:
        assert session.get(ProduceBatch, UUID(batch["id"])) is not None
        assert session.scalar(select(func.count()).select_from(ProduceBatch)) == 1


def test_patch_archive(produce_client):
    batch = create(produce_client)
    response = produce_client.patch(
        f"/api/v1/produce/{batch['id']}", headers=headers(), json={"status": "archived"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "archived" and response.json()["archived_at"]


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_owner_isolation_and_unknown_ids(produce_client, method):
    batch = create(produce_client)
    kwargs = {"json": {"notes": "Changed by B"}} if method == "patch" else {}
    assert (
        getattr(produce_client, method)(
            f"/api/v1/produce/{batch['id']}", headers=headers("b"), **kwargs
        ).status_code
        == 404
    )
    assert (
        getattr(produce_client, method)(
            f"/api/v1/produce/{UUID(int=999)}", headers=headers(), **kwargs
        ).status_code
        == 404
    )
    assert (
        produce_client.get(f"/api/v1/produce/{batch['id']}", headers=headers()).json()["notes"]
        == PAYLOAD["notes"]
    )
    assert (
        produce_client.get(f"/api/v1/produce?farmer_id={A}", headers=headers("b")).json()["items"]
        == []
    )


@pytest.mark.parametrize(
    "method,path",
    [("post", ""), ("get", ""), ("get", f"/{A}"), ("patch", f"/{A}"), ("delete", f"/{A}")],
)
def test_unauthenticated_operations(produce_client, method, path):
    kwargs = (
        {"json": PAYLOAD}
        if method == "post"
        else {"json": {"quantity_kg": 10}}
        if method == "patch"
        else {}
    )
    assert getattr(produce_client, method)("/api/v1/produce" + path, **kwargs).status_code == 401


@pytest.mark.parametrize(
    "invalid",
    [
        {"quantity_kg": 0},
        {"quantity_kg": -1},
        {"quantity_kg": "NaN"},
        {"quantity_kg": "Infinity"},
        {"quantity_kg": "1.0001"},
        {"quantity_kg": 1000001},
        {"quantity_kg": None},
        {"crop_id": "not-in-catalog"},
        {"crop_id": "bad/id"},
        {"harvested_at": (NOW + timedelta(days=1)).isoformat()},
        {"sell_by": (NOW - timedelta(days=1)).isoformat()},
        {"sell_by": PAYLOAD["harvested_at"]},
        {"harvested_at": "2026-11-01T10:00:00"},
        {"sell_by": "2026-11-31T10:00:00Z"},
        {"status": "unknown"},
        {"status": "expired"},
        {"quality": "x" * 81},
        {"notes": "x" * 2001},
        {"notes": "bad\x00text"},
        {"source_location": {"district": "Unknown", "town": "Test"}},
        {"source_location": {"district": "Kandy", "town": "x"}},
        {"source_location": {"district": "Kandy", "town": "Test", "latitude": 7}},
        {"source_location": {"district": "Kandy", "town": "Test", "latitude": 91, "longitude": 80}},
        {
            "source_location": {
                "district": "Kandy",
                "town": "Test",
                "latitude": 7,
                "longitude": -181,
            }
        },
        {"farmer_id": str(B)},
        {"id": str(B)},
    ],
)
def test_create_validation_has_no_side_effects(produce_client, produce_app, invalid):
    response = produce_client.post("/api/v1/produce", headers=headers(), json=PAYLOAD | invalid)
    assert response.status_code == 422
    with produce_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProduceBatch)) == 0


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"quantity_kg": None},
        {"crop_id": None},
        {"harvested_at": None},
        {"sell_by": None},
        {"status": None},
        {"quantity_kg": -1},
        {"crop_id": "missing"},
        {"farmer_id": str(B)},
        {"sell_by": PAYLOAD["harvested_at"]},
    ],
)
def test_patch_validation_preserves_existing_batch(produce_client, invalid):
    batch = create(produce_client)
    url = f"/api/v1/produce/{batch['id']}"
    assert produce_client.patch(url, headers=headers(), json=invalid).status_code == 422
    assert produce_client.get(url, headers=headers()).json()["quantity_kg"] == batch["quantity_kg"]


def test_inactive_crop_can_be_read_but_not_newly_selected(produce_client, produce_app):
    batch = create(produce_client)
    with produce_app.state.session_factory() as session:
        session.get(Crop, "tomato").active = False
        session.commit()
    assert (
        produce_client.post("/api/v1/produce", headers=headers(), json=PAYLOAD).status_code == 422
    )
    assert (
        produce_client.patch(
            f"/api/v1/produce/{batch['id']}", headers=headers(), json={"notes": "Still editable"}
        ).status_code
        == 200
    )
    second = create(produce_client, crop_id="carrot")
    assert (
        produce_client.patch(
            f"/api/v1/produce/{second['id']}", headers=headers(), json={"crop_id": "tomato"}
        ).status_code
        == 422
    )


def test_profile_required_even_for_explicit_location(produce_client, produce_app):
    with produce_app.state.session_factory() as session:
        session.delete(session.get(FarmerProfile, A))
        session.commit()
    response = produce_client.post(
        "/api/v1/produce",
        headers=headers(),
        json=PAYLOAD | {"source_location": {"district": "Kandy", "town": "Test town"}},
    )
    assert response.status_code == 409


def test_expiration_is_persisted_and_owner_scoped(produce_client, produce_app):
    batch = create(produce_client)
    other = produce_client.post("/api/v1/produce", headers=headers("b"), json=PAYLOAD).json()
    with produce_app.state.session_factory() as session:
        for id in [batch["id"], other["id"]]:
            row = session.get(ProduceBatch, UUID(id))
            row.harvested_at = NOW - timedelta(days=2)
            row.sell_by = NOW - timedelta(days=1)
        session.commit()
    response = produce_client.get("/api/v1/produce?status=expired", headers=headers()).json()
    assert response["total"] == 1 and response["items"][0]["status"] == "expired"
    with produce_app.state.session_factory() as session:
        assert session.get(ProduceBatch, UUID(batch["id"])).status == "expired"
        assert session.get(ProduceBatch, UUID(other["id"])).status == "available"
    assert (
        produce_client.patch(
            f"/api/v1/produce/{batch['id']}", headers=headers(), json={"status": "available"}
        ).status_code
        == 422
    )
    extended = produce_client.patch(
        f"/api/v1/produce/{batch['id']}",
        headers=headers(),
        json={"status": "available", "sell_by": PAYLOAD["sell_by"]},
    )
    assert extended.status_code == 200


def test_historical_sold_and_expired_are_valid(produce_client):
    for status in ["sold", "expired", "archived"]:
        batch = create(
            produce_client,
            status=status,
            harvested_at=(NOW - timedelta(days=3)).isoformat(),
            sell_by=(NOW - timedelta(days=1)).isoformat(),
        )
        assert batch["status"] == status


def test_search_pagination_and_status_filters(produce_client):
    create(produce_client, notes="Unique note alpha")
    create(produce_client, crop_id="carrot", status="reserved", notes="Other note")
    for query, count in [
        ("q=තක්කාලි", 1),
        ("q=கரட்", 1),
        ("q=ALPHA", 1),
        ("q=Gampola", 2),
        ("q=missing", 0),
        ("q=%", 0),
        ("status=reserved", 1),
        ("status=sold", 0),
    ]:
        assert (
            produce_client.get("/api/v1/produce?" + query, headers=headers()).json()["total"]
            == count
        )
    first = produce_client.get("/api/v1/produce?page_size=1", headers=headers()).json()
    second = produce_client.get("/api/v1/produce?page_size=1&page=2", headers=headers()).json()
    assert first["total"] == 2 and first["items"][0]["id"] != second["items"][0]["id"]
    assert produce_client.get("/api/v1/produce?page=99", headers=headers()).json()["items"] == []


@pytest.mark.parametrize("query", ["page=0", "page_size=101", "status=invalid", "q=" + "a" * 101])
def test_invalid_list_filters(produce_client, query):
    assert produce_client.get("/api/v1/produce?" + query, headers=headers()).status_code == 422


def test_repository_service_and_database_constraints(produce_app):
    with produce_app.state.session_factory() as session:
        service = ProduceService(ProduceRepository(session))
        batch = service.create(A, ProduceCreate(**PAYLOAD))
        assert service.get(A, batch.id).crop_id == "tomato"
    with produce_app.state.session_factory() as session:
        row = ProduceRepository(session).get(A, batch.id)
        row.quantity_kg = -1
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        row = session.get(ProduceBatch, batch.id)
        row.sell_by = row.harvested_at
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_database_failure_is_safe(produce_client, monkeypatch):
    batch = create(produce_client)

    def unavailable(*args, **kwargs):
        raise OperationalError("private database", {}, Exception("secret"))

    monkeypatch.setattr(ProduceRepository, "get", unavailable)
    monkeypatch.setattr(ProduceRepository, "list", unavailable)
    monkeypatch.setattr(ProduceService, "require_crop", unavailable)
    for method, path, kwargs in [
        ("post", "", {"json": PAYLOAD}),
        ("get", "", {}),
        ("get", f"/{batch['id']}", {}),
        ("patch", f"/{batch['id']}", {"json": {"notes": "New"}}),
        ("delete", f"/{batch['id']}", {}),
    ]:
        response = getattr(produce_client, method)(
            "/api/v1/produce" + path, headers=headers(), **kwargs
        )
        assert response.status_code == 503 and "secret" not in response.text


@pytest.mark.parametrize("quantity", ["0.001", "1000000"])
def test_quantity_boundaries_offset_timestamps_and_approximate_location(produce_client, quantity):
    offset = timezone(timedelta(hours=5, minutes=30))
    batch = create(
        produce_client,
        quantity_kg=quantity,
        harvested_at=(NOW - timedelta(hours=2)).astimezone(offset).isoformat(),
        sell_by=(NOW + timedelta(days=2)).astimezone(offset).isoformat(),
        source_location={
            "district": "Matale",
            "town": "Dambulla",
            "latitude": 7.123456,
            "longitude": 80.123456,
        },
    )
    assert Decimal(batch["quantity_kg"]) == Decimal(quantity)
    assert batch["source_location"]["latitude"] == 7.123
    assert batch["source_location"]["longitude"] == 80.123
    assert batch["harvested_at"].endswith("Z") and batch["sell_by"].endswith("Z")
