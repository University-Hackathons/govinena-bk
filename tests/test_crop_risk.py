from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.auth import Identity, verify_identity
from app.db.seed_catalog import seed_catalog
from app.models import Base, CropRiskProfile, FarmerProfile, ProduceBatch, User
from app.schemas.crop_risk import RiskRules
from app.services.crop_risk import evaluate_risk

A, B, BATCH = UUID(int=701), UUID(int=702), UUID(int=703)
HARVEST = datetime(2026, 11, 16, 0, tzinfo=UTC)
RULES = {
    "expected_selling_window_hours": 48,
    "loss_percent_per_day": {"ambient": "10", "cooled": "2"},
    "rationale": "Test scenario assumptions only; not validated crop guidance.",
}


def scenario(hours=1, **changes):
    return evaluate_risk(
        **(
            dict(
                batch_id=BATCH,
                crop_id="tomato",
                quantity=Decimal(100),
                harvested_at=HARVEST,
                deadline=HARVEST + timedelta(hours=48),
                assessment_at=HARVEST + timedelta(hours=hours),
                arrival_at=None,
                storage="ambient",
                rules=RiskRules(**RULES),
                profile_version=1,
            )
            | changes
        )
    )


@pytest.mark.parametrize(
    "hours,urgency",
    [
        (0, "low"),
        (1, "low"),
        (24, "medium"),
        (36, "high"),
        (47, "high"),
        (48, "high"),
        (49, "high"),
    ],
)
def test_fresh_medium_near_and_elapsed_deadlines(hours, urgency):
    result = scenario(hours)
    assert result.urgency == urgency
    assert result.remaining_selling_seconds == max(0, (48 - hours) * 3600)
    assert result.alternative_accepted == (hours < 48)
    assert result.deadline_status == (
        "passed" if hours > 48 else "reached" if hours == 48 else "open"
    )
    assert any("not scientifically validated" in text for text in result.assumptions)


def test_longer_delay_higher_loss_and_bounded_quantities():
    fresh, later, very_late = scenario(1), scenario(24), scenario(300)
    assert fresh.estimated_saleable_quantity_kg > later.estimated_saleable_quantity_kg
    assert later.assumed_loss_percent == 10
    assert very_late.assumed_loss_percent == 100
    assert very_late.estimated_saleable_quantity_kg is None
    assert scenario(24, storage="cooled").estimated_saleable_quantity_kg == 98
    assert scenario(24, storage="shaded").estimated_saleable_quantity_kg is None


@pytest.mark.parametrize(
    "offset,accepted", [(-0.000001, True), (0, False), (0.000001, False), (3600, False)]
)
def test_arrival_deadline_boundary(offset, accepted):
    result = scenario(arrival_at=HARVEST + timedelta(hours=48, seconds=offset))
    assert result.alternative_accepted == accepted
    assert result.deadline_status == "open"
    assert result.rejection_reason == (None if accepted else "arrival_at_or_after_deadline")
    if not accepted:
        assert result.estimated_saleable_quantity_kg is None


def test_missing_disabled_and_guidance_does_not_extend_deadline():
    missing = scenario(rules=None, profile_version=None)
    assert missing.guidance_status == "missing"
    assert missing.estimated_saleable_quantity_kg is None and missing.urgency == "low"
    disabled = scenario(rules=RiskRules(**RULES, enabled=False))
    assert disabled.guidance_status == "disabled" and disabled.assumed_loss_percent is None
    short = scenario(25, rules=RiskRules(**(RULES | {"expected_selling_window_hours": 24})))
    assert short.urgency == "high" and short.alternative_accepted
    assert (
        scenario(
            49, rules=RiskRules(**(RULES | {"expected_selling_window_hours": 168}))
        ).alternative_accepted
        is False
    )


@pytest.fixture
def risk_app(application):
    Base.metadata.create_all(application.state.engine)
    with application.state.session_factory() as session:
        seed_catalog(session)
        session.add_all([User(id=id, email=f"{id}@example.test") for id in (A, B)])
        session.commit()
        session.add(
            FarmerProfile(
                user_id=A,
                full_name="Risk farmer",
                district="Kandy",
                town="Gampola",
                preferred_language="en",
                transport_mode="hired_vehicle",
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
                harvested_at=HARVEST,
                sell_by=HARVEST + timedelta(hours=48),
                source_district="Kandy",
                source_town="Gampola",
                status="available",
            )
        )
        session.commit()
    application.state.settings.admin_user_ids = [A]

    def identity(request: Request):
        id = {"Bearer a": A, "Bearer b": B}.get(request.headers.get("authorization"))
        if id is None:
            raise HTTPException(401)
        return Identity(id, id, f"{id}@example.test")

    application.dependency_overrides[verify_identity] = identity
    return application


@pytest.fixture
def client(risk_app):
    with TestClient(risk_app) as client:
        yield client


HEADERS = {"Authorization": "Bearer a"}
PAYLOAD = {
    "produce_batch_id": str(BATCH),
    "assessment_at": "2026-11-16T01:00:00Z",
    "storage_condition": "ambient",
}


def test_api_missing_rules_then_versioned_edits_and_disable(client, risk_app):
    empty = client.post("/api/v1/crop-risk/evaluate", headers=HEADERS, json=PAYLOAD)
    assert empty.status_code == 200 and empty.json()["guidance_status"] == "missing"
    assert empty.headers["cache-control"] == "private, no-store"
    assert client.get("/api/v1/crop-risk/profiles/tomato/versions", headers=HEADERS).json() == []
    for version, rules in [
        (1, RULES),
        (2, RULES | {"loss_percent_per_day": {"ambient": "20"}}),
        (3, RULES | {"enabled": False}),
    ]:
        response = client.post("/api/v1/crop-risk/profiles/tomato", headers=HEADERS, json=rules)
        assert response.status_code == 201, response.text
        assert response.json()["version"] == version
        result = client.post("/api/v1/crop-risk/evaluate", headers=HEADERS, json=PAYLOAD).json()
        assert result["profile_version"] == version
        assert result["guidance_status"] == ("disabled" if version == 3 else "configured")
    versions = client.get("/api/v1/crop-risk/profiles/tomato/versions", headers=HEADERS).json()
    assert [item["version"] for item in versions] == [3, 2, 1]
    assert versions[-1]["loss_percent_per_day"]["ambient"] == "10"
    with risk_app.state.session_factory() as session:
        assert len(session.scalars(select(CropRiskProfile)).all()) == 3
    assert (
        client.get("/api/v1/crop-risk/profiles/tomato", headers=HEADERS).json()["enabled"] is False
    )


def test_auth_ownership_unknown_and_read_only_references(client):
    assert client.post("/api/v1/crop-risk/evaluate", json=PAYLOAD).status_code == 401
    assert (
        client.post(
            "/api/v1/crop-risk/evaluate", headers={"Authorization": "Bearer b"}, json=PAYLOAD
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/crop-risk/evaluate",
            headers=HEADERS,
            json=PAYLOAD | {"produce_batch_id": str(UUID(int=999))},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/crop-risk/profiles/tomato", headers={"Authorization": "Bearer b"}, json=RULES
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/crop-risk/profiles/tomato/versions", headers={"Authorization": "Bearer b"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/crop-risk/profiles/tomato").status_code == 401
    assert client.get("/api/v1/crop-risk/profiles/tomato", headers=HEADERS).status_code == 404
    assert (
        client.post("/api/v1/crop-risk/profiles/unknown", headers=HEADERS, json=RULES).status_code
        == 404
    )
    assert (
        client.patch("/api/v1/crop-risk/profiles/tomato", headers=HEADERS, json=RULES).status_code
        == 405
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"assessment_at": "2026-11-16T01:00:00"},
        {"expected_arrival_at": "2026-11-16T02:00:00"},
        {"assessment_at": "2026-11-15T00:00:00Z"},
        {"expected_arrival_at": "2026-11-16T00:59:59Z"},
        {"storage_condition": "magic"},
        {"assessment_at": "0001-01-01T00:00:00+05:30"},
        {"crop_id": "carrot"},
        {"quantity_kg": -1},
    ],
)
def test_invalid_evaluation(client, changes):
    assert (
        client.post(
            "/api/v1/crop-risk/evaluate", headers=HEADERS, json=PAYLOAD | changes
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_selling_window_hours": 0},
        {"high_remaining_percent": 60},
        {"loss_percent_per_day": {"ambient": -1}},
        {"loss_percent_per_day": {"ambient": "NaN"}},
        {"loss_percent_per_day": {"ambient": 101}},
        {"loss_percent_per_day": {"invalid": 10}},
        {"rationale": ""},
        {"scientifically_validated": True},
    ],
)
def test_invalid_profile(client, changes):
    assert (
        client.post(
            "/api/v1/crop-risk/profiles/tomato", headers=HEADERS, json=RULES | changes
        ).status_code
        == 422
    )


def test_timezone_equivalence_and_closed_batches(client, risk_app):
    utc_result = client.post("/api/v1/crop-risk/evaluate", headers=HEADERS, json=PAYLOAD).json()
    sl_result = client.post(
        "/api/v1/crop-risk/evaluate",
        headers=HEADERS,
        json=PAYLOAD | {"assessment_at": "2026-11-16T06:30:00+05:30"},
    ).json()
    assert utc_result == sl_result
    for status in ("sold", "archived"):
        with risk_app.state.session_factory() as session:
            batch = session.get(ProduceBatch, BATCH)
            batch.status = status
            batch.archived_at = HARVEST if status == "archived" else None
            session.commit()
        result = client.post("/api/v1/crop-risk/evaluate", headers=HEADERS, json=PAYLOAD).json()
        assert (
            result["alternative_accepted"] is False and result["rejection_reason"] == "batch_closed"
        )


def test_default_assessment_clock(client, monkeypatch):
    import app.api.v1.endpoints.crop_risk as endpoint

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return HARVEST + timedelta(hours=2)

    monkeypatch.setattr(endpoint, "datetime", Clock)
    result = client.post(
        "/api/v1/crop-risk/evaluate", headers=HEADERS, json={"produce_batch_id": str(BATCH)}
    ).json()
    assert result["assessed_at"] == "2026-11-16T02:00:00Z"


def test_database_failure_sanitized(client, monkeypatch):
    def fail(*args, **kwargs):
        raise OperationalError("private", {}, Exception("secret"))

    monkeypatch.setattr("app.api.v1.endpoints.crop_risk.latest", fail)
    response = client.post("/api/v1/crop-risk/evaluate", headers=HEADERS, json=PAYLOAD)
    assert response.status_code == 503 and "secret" not in response.text


def test_explicit_zero_loss_is_not_missing_and_arrival_uses_full_delay():
    zero = scenario(24, rules=RiskRules(**(RULES | {"loss_percent_per_day": {"ambient": 0}})))
    assert zero.estimated_saleable_quantity_kg == 100 and zero.assumed_loss_percent == 0
    arriving = scenario(1, arrival_at=HARVEST + timedelta(hours=24))
    assert arriving.estimated_saleable_quantity_kg == scenario(24).estimated_saleable_quantity_kg
    assert arriving.remaining_at_arrival_seconds == 24 * 3600


def test_api_arrival_rejection_does_not_move_user_deadline(client):
    client.post("/api/v1/crop-risk/profiles/tomato", headers=HEADERS, json=RULES)
    result = client.post(
        "/api/v1/crop-risk/evaluate",
        headers=HEADERS,
        json=PAYLOAD
        | {
            "expected_arrival_at": "2026-11-18T05:30:00+05:30",
        },
    ).json()
    assert result["deadline_status"] == "open"
    assert result["user_deadline"] == "2026-11-18T00:00:00Z"
    assert result["alternative_accepted"] is False
    assert result["estimated_saleable_quantity_kg"] is None
    assert result["profile_version"] == 1
