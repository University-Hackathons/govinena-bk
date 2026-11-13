from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.auth import Identity, verify_identity
from app.models import Base, FarmerProfile, User
from app.repositories.farmer import FarmerProfileRepository
from app.schemas.farmer import FarmerProfileInput
from app.services.farmer import FarmerProfileService

A = UUID(int=101)
B = UUID(int=102)
PAYLOAD = {
    "full_name": "Test Farmer",
    "district": "Kandy",
    "town": "Gampola",
    "preferred_language": "si",
    "transport_mode": "hired_vehicle",
}


@pytest.fixture
def farmer_app(application):
    Base.metadata.create_all(application.state.engine)

    def identity(request: Request):
        token = request.headers.get("authorization")
        user_id = {"Bearer user-a": A, "Bearer user-b": B}.get(token)
        if user_id is None:
            raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
        return Identity(user_id, user_id, f"{user_id}@example.test")

    application.dependency_overrides[verify_identity] = identity
    return application


@pytest.fixture
def farmer_client(farmer_app):
    with TestClient(farmer_app) as client:
        yield client


def headers(user="a"):
    return {"Authorization": f"Bearer user-{user}"}


def test_create_update_and_reload_profile(farmer_client, farmer_app):
    assert farmer_client.get("/api/v1/farmer/profile", headers=headers()).status_code == 404
    response = farmer_client.put("/api/v1/farmer/profile", headers=headers(), json=PAYLOAD)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["user_id"] == str(A)
    assert body["full_name"] == PAYLOAD["full_name"]
    assert body["completion"] == {
        "required_complete": True,
        "optional_completed": 0,
        "optional_total": 5,
        "percentage": 50,
    }
    update = PAYLOAD | {
        "full_name": "Updated Farmer",
        "contact_number": "+94 (77) 123-4567",
        "latitude": 7.167891,
        "longitude": 80.576789,
        "vehicle_capacity_kg": "200.50",
        "estimated_transport_rate": "0.00",
        "preferred_market_ids": ["market_1", "market_2", "market_1"],
    }
    saved = farmer_client.put("/api/v1/farmer/profile", headers=headers(), json=update)
    assert saved.status_code == 200
    body = saved.json()
    assert body["completion"]["percentage"] == 100
    assert body["contact_number"] == "+94771234567"
    assert body["latitude"] == 7.168 and body["longitude"] == 80.577
    assert body["preferred_market_ids"] == ["market_1", "market_2"]
    assert Decimal(body["vehicle_capacity_kg"]) == Decimal("200.50")
    assert farmer_client.get("/api/v1/farmer/profile", headers=headers()).json() == body
    with farmer_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FarmerProfile)) == 1
        assert session.get(FarmerProfile, A).full_name == "Updated Farmer"
    # PUT replaces the profile: omitting optional fields deliberately clears them.
    cleared = farmer_client.put("/api/v1/farmer/profile", headers=headers(), json=PAYLOAD)
    assert cleared.json()["contact_number"] is None
    assert cleared.json()["preferred_market_ids"] == []


def test_owner_isolation_and_no_mass_assignment(farmer_client):
    farmer_client.put("/api/v1/farmer/profile", headers=headers(), json=PAYLOAD)
    assert farmer_client.get("/api/v1/farmer/profile", headers=headers("b")).status_code == 404
    b_data = PAYLOAD | {"full_name": "Second Farmer", "town": "Peradeniya"}
    b = farmer_client.put("/api/v1/farmer/profile", headers=headers("b"), json=b_data)
    assert b.json()["user_id"] == str(B)
    assert (
        farmer_client.get("/api/v1/farmer/profile", headers=headers()).json()["full_name"]
        == "Test Farmer"
    )
    assert (
        farmer_client.put(
            "/api/v1/farmer/profile",
            headers=headers(),
            json=PAYLOAD | {"user_id": str(B)},
        ).status_code
        == 422
    )
    query = farmer_client.get(f"/api/v1/farmer/profile?user_id={B}", headers=headers())
    assert query.json()["user_id"] == str(A)
    assert farmer_client.get(f"/api/v1/farmer/profile/{B}", headers=headers()).status_code == 404


@pytest.mark.parametrize("method", ["get", "put"])
def test_unauthorized_requests_do_not_change_profiles(farmer_client, farmer_app, method):
    kwargs = {"json": PAYLOAD} if method == "put" else {}
    assert getattr(farmer_client, method)("/api/v1/farmer/profile", **kwargs).status_code == 401
    with farmer_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FarmerProfile)) == 0


@pytest.mark.parametrize(
    "invalid",
    [
        {"full_name": " "},
        {"full_name": "bad\nname"},
        {"town": ""},
        {"district": "Unknown"},
        {"contact_number": "123"},
        {"contact_number": "abc123456789"},
        {"vehicle_capacity_kg": -1},
        {"vehicle_capacity_kg": 0},
        {"vehicle_capacity_kg": "1.001"},
        {"vehicle_capacity_kg": "NaN"},
        {"estimated_transport_rate": -1},
        {"estimated_transport_rate": "Infinity"},
        {"estimated_transport_rate": "0.123"},
        {"estimated_transport_rate": 1000001},
        {"latitude": 91, "longitude": 80},
        {"latitude": 7},
        {"longitude": 80},
        {"latitude": "NaN", "longitude": 80},
        {"latitude": 7, "longitude": -181},
        {"preferred_language": "xx"},
        {"transport_mode": "bus"},
        {"preferred_market_ids": ["bad/id"]},
        {"preferred_market_ids": [str(i) for i in range(21)]},
        {"unexpected": "value"},
    ],
)
def test_api_validation(farmer_client, invalid):
    response = farmer_client.put(
        "/api/v1/farmer/profile", headers=headers(), json=PAYLOAD | invalid
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert farmer_client.get("/api/v1/farmer/profile", headers=headers()).status_code == 404


def test_invalid_json(farmer_client):
    response = farmer_client.put(
        "/api/v1/farmer/profile",
        headers=headers(),
        content="{invalid",
    )
    assert response.status_code == 422


def test_repository_service_persist_across_sessions(application):
    Base.metadata.create_all(application.state.engine)
    with application.state.session_factory() as session:
        session.add_all([User(id=A, email="a@example.test"), User(id=B, email="b@example.test")])
        session.commit()
        service = FarmerProfileService(FarmerProfileRepository(session))
        assert service.get(A) is None
        profile = service.put(A, FarmerProfileInput(**PAYLOAD))
        assert profile.user_id == A
    with application.state.session_factory() as session:
        repository = FarmerProfileRepository(session)
        assert repository.for_user(A).full_name == "Test Farmer"
        assert repository.for_user(B) is None
        service = FarmerProfileService(repository)
        service.put(A, FarmerProfileInput(**(PAYLOAD | {"town": "Updated Town"})))
    with application.state.session_factory() as session:
        assert FarmerProfileRepository(session).for_user(A).town == "Updated Town"


def test_database_rejects_duplicate_profile_and_negative_capacity(application):
    Base.metadata.create_all(application.state.engine)
    with application.state.session_factory() as session:
        session.add(User(id=A, email="a@example.test"))
        session.commit()
        service = FarmerProfileService(FarmerProfileRepository(session))
        service.put(A, FarmerProfileInput(**PAYLOAD))
        session.add(FarmerProfile(user_id=A, **PAYLOAD, preferred_market_ids=[]))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.get(FarmerProfile, A).vehicle_capacity_kg = -1
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_schema_optional_contact_formats_and_defaults():
    assert (
        FarmerProfileInput(**(PAYLOAD | {"contact_number": "077 123 4567"})).contact_number
        == "0771234567"
    )
    assert FarmerProfileInput(**(PAYLOAD | {"contact_number": ""})).contact_number is None
    with pytest.raises(ValidationError):
        FarmerProfileInput(**(PAYLOAD | {"full_name": "a" * 121}))


def test_storage_failure_is_safe(farmer_client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("private database connection details")

    monkeypatch.setattr(FarmerProfileRepository, "for_user", unavailable)
    response = farmer_client.get("/api/v1/farmer/profile", headers=headers())
    assert response.status_code == 500
    assert response.json()["error"]["message"] == "Internal server error"
    assert "private" not in response.text
