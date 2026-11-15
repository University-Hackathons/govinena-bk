import csv
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.core.auth import Identity, verify_identity
from app.db.seed_catalog import seed_catalog
from app.models import Base, MarketPriceObservation, PriceImport, PriceImportRow
from app.providers.market_prices import COLUMNS, VerifiedCSVProvider
from app.repositories.prices import PriceRepository
from app.schemas.prices import SL

ADMIN, FARMER = UUID(int=501), UUID(int=502)
SAMPLE = Path("app/data/verified_prices_sample.csv").read_bytes()
NOTE = "Manually checked against the cited source document."


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def row(**changes):
    now = datetime.now(UTC)
    result = dict.fromkeys(COLUMNS, "")
    result.update(
        crop_id="tomato",
        market_id="dambulla-dec",
        observed_at=(now - timedelta(hours=1)).isoformat(),
        price="120",
        unit="kg",
        unit_quantity="1",
        currency="LKR",
        price_type="wholesale",
        source_name="Test source",
        source_url="https://example.org/verified-document",
        source_publication_date=now.astimezone(SL).date().isoformat(),
    )
    return result | changes


def headers(user="admin"):
    return {"Authorization": f"Bearer {user}", "Content-Type": "text/csv"}


@pytest.fixture
def prices_app(application):
    Base.metadata.create_all(application.state.engine)
    application.state.settings.admin_user_ids = [ADMIN]
    with application.state.session_factory() as session:
        seed_catalog(session)

    def identity(request: Request):
        user = {"Bearer admin": ADMIN, "Bearer farmer": FARMER}.get(
            request.headers.get("authorization")
        )
        if user is None:
            raise HTTPException(401)
        return Identity(user, user, f"{user}@example.test")

    application.dependency_overrides[verify_identity] = identity
    return application


@pytest.fixture
def prices_client(prices_app):
    with TestClient(prices_app) as client:
        yield client


def upload(client, data=SAMPLE, **params):
    return client.post(
        "/api/v1/prices/imports",
        params={"verified": "true", "verification_note": NOTE, "filename": "sample.csv"} | params,
        headers=headers(),
        content=data,
    )


def test_verified_sample_import_is_audited_and_idempotent(prices_client, prices_app):
    result = upload(prices_client)
    assert result.status_code == 200, result.text
    report = result.json()
    assert (report["accepted"], report["duplicates"], report["rejected"]) == (16, 0, 0)
    again = upload(prices_client).json()
    assert again["replayed"] is True and again["id"] == report["id"]
    audit = prices_client.get(
        f"/api/v1/prices/imports/{report['id']}?page_size=5", headers=headers()
    ).json()
    assert audit["total"] == 16 and len(audit["items"]) == 5
    assert audit["items"][0]["raw_data"]["unit"] == "kg"
    with prices_app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MarketPriceObservation)) == 16
        assert session.get(PriceImport, UUID(report["id"])).original_file == SAMPLE
        assert session.scalar(select(func.count()).select_from(PriceImportRow)) == 16


def test_latest_history_type_and_date_filters(prices_client):
    upload(prices_client)
    wholesale = prices_client.get("/api/v1/prices/latest?crop_id=tomato", headers=headers()).json()
    assert wholesale["total"] == 1
    latest = wholesale["items"][0]
    assert Decimal(latest["price_per_kg"]) == Decimal("345")
    assert latest["freshness"] == "stale" and latest["observation_precision"] == "day"
    assert latest["source_published_at"] is None
    assert latest["source_publication_date"] == "2025-12-30"
    retail = prices_client.get(
        "/api/v1/prices/latest?crop_id=tomato&price_type=retail", headers=headers()
    ).json()
    assert Decimal(retail["items"][0]["price_per_kg"]) == Decimal("375")
    history = prices_client.get(
        "/api/v1/prices/history?crop_id=tomato&market_id=dambulla-dec&start_date=2025-12-29&end_date=2025-12-30",
        headers=headers(),
    ).json()
    assert history["total"] == 2
    assert [Decimal(item["price_per_kg"]) for item in history["items"]] == [375, 345]
    single = prices_client.get(
        "/api/v1/prices/history?crop_id=tomato&market_id=dambulla-dec&start_date=2025-12-30&end_date=2025-12-30",
        headers=headers(),
    ).json()
    assert single["total"] == 1
    limited = prices_client.get(
        "/api/v1/prices/history?crop_id=tomato&market_id=dambulla-dec&start_date=2025-12-29&end_date=2025-12-30&limit=1",
        headers=headers(),
    ).json()
    assert limited["truncated"] is True and len(limited["items"]) == 1


def test_comparison_and_freshness_preserve_missing_coverage(prices_client):
    upload(prices_client)
    result = prices_client.get("/api/v1/prices/comparison?crop_id=tomato", headers=headers()).json()
    assert result["total"] == 3
    assert sum(bool(item["observations"]) for item in result["items"]) == 1
    summary = prices_client.get("/api/v1/prices/freshness?crop_id=tomato", headers=headers()).json()
    assert summary["stale_series"] == 1 and summary["fresh_series"] == 0
    assert summary["expected_market_crop_pairs"] == 3
    assert summary["pairs_without_reported_price"] == 2
    assert summary["last_observed_at"] < summary["last_imported_at"]


def test_missing_is_not_zero_and_latest_missing_does_not_fall_back(prices_client):
    now = datetime.now(UTC)
    rows = [
        row(price="0", observed_at=(now - timedelta(hours=2)).isoformat()),
        row(price="", observed_at=(now - timedelta(hours=1)).isoformat()),
    ]
    assert upload(prices_client, csv_bytes(rows)).json()["accepted"] == 2
    latest = prices_client.get("/api/v1/prices/latest?crop_id=tomato", headers=headers()).json()[
        "items"
    ][0]
    assert latest["price_per_kg"] is None and latest["freshness"] == "missing"
    today = now.astimezone(SL).date().isoformat()
    start = (now - timedelta(hours=2)).astimezone(SL).date().isoformat()
    history = prices_client.get(
        f"/api/v1/prices/history?crop_id=tomato&market_id=dambulla-dec&start_date={start}&end_date={today}",
        headers=headers(),
    ).json()["items"]
    assert Decimal(history[0]["price_per_kg"]) == 0 and history[1]["price_per_kg"] is None
    summary = prices_client.get("/api/v1/prices/freshness?crop_id=tomato", headers=headers()).json()
    assert summary["missing_series"] == 1 and summary["pairs_without_reported_price"] == 3


@pytest.mark.parametrize(
    "unit,quantity,price,expected",
    [
        ("kg", "50", "5000", "100"),
        ("g", "500", "50", "100"),
        ("tonne", "1", "100000", "100"),
        ("kg", "1", "0", "0"),
    ],
)
def test_units_are_normalized_and_raw_source_retained(
    prices_client, unit, quantity, price, expected
):
    report = upload(
        prices_client, csv_bytes([row(unit=unit, unit_quantity=quantity, price=price)])
    ).json()
    assert report["accepted"] == 1
    latest = prices_client.get("/api/v1/prices/latest", headers=headers()).json()["items"][0]
    assert Decimal(latest["price_per_kg"]) == Decimal(expected)
    audit = prices_client.get(f"/api/v1/prices/imports/{report['id']}", headers=headers()).json()
    assert audit["items"][0]["raw_data"]["unit"] == unit


@pytest.mark.parametrize(
    "invalid",
    [
        {"price": "-1"},
        {"price": "NaN"},
        {"price": "Infinity"},
        {"unit_quantity": "0"},
        {"unit_quantity": "-1"},
        {"unit": "bag"},
        {"currency": "USD"},
        {"price_type": "auction"},
        {"crop_id": "unknown"},
        {"market_id": "unknown"},
        {"source_name": ""},
        {"source_url": "", "document_reference": ""},
        {"source_url": "javascript:alert(1)"},
        {"observed_at": "2025-12-30T10:00:00"},
        {"observed_at": "2099-01-01T00:00:00Z"},
        {"source_publication_date": "2000-01-01"},
        {"observation_date": "2025-12-30"},
        {"observed_at": "", "observation_date": ""},
        {"source_published_at": "2099-01-01T00:00:00Z"},
    ],
)
def test_invalid_rows_are_rejected_and_logged(prices_client, invalid):
    response = upload(prices_client, csv_bytes([row(**invalid)]))
    assert response.status_code == 200
    report = response.json()
    assert report["rejected"] == 1 and report["accepted"] == 0
    audit = prices_client.get(f"/api/v1/prices/imports/{report['id']}", headers=headers()).json()
    assert audit["items"][0]["outcome"] == "rejected" and audit["items"][0]["error_code"]
    assert prices_client.get("/api/v1/prices/latest", headers=headers()).json()["total"] == 0


def test_duplicate_and_conflicting_rows_do_not_overwrite(prices_client):
    first = row()
    report = upload(prices_client, csv_bytes([first, first, first | {"price": "999"}])).json()
    assert (report["accepted"], report["duplicates"], report["rejected"]) == (1, 1, 1)
    new_file = upload(prices_client, csv_bytes([first | {"unit_quantity": "1.0"}])).json()
    assert new_file["duplicates"] == 1 and not new_file["replayed"]
    latest = prices_client.get("/api/v1/prices/latest", headers=headers()).json()["items"]
    assert len(latest) == 1 and Decimal(latest[0]["price_per_kg"]) == 120


def test_source_and_grade_series_are_separate(prices_client):
    first = row()
    report = upload(
        prices_client,
        csv_bytes(
            [
                first,
                first | {"quality": "A", "price": "150"},
                first | {"source_name": "Another test source", "price": "170"},
            ]
        ),
    ).json()
    assert report["accepted"] == 3
    latest = prices_client.get("/api/v1/prices/latest", headers=headers()).json()
    assert latest["total"] == 3
    assert (
        prices_client.get("/api/v1/prices/latest?quality=A", headers=headers()).json()["total"] == 1
    )
    assert (
        prices_client.get("/api/v1/prices/latest?quality=", headers=headers()).json()["total"] == 2
    )
    assert (
        prices_client.get(
            "/api/v1/prices/latest?source_name=Another%20test%20source", headers=headers()
        ).json()["total"]
        == 1
    )


@pytest.mark.parametrize(
    "data", [b"", b"bad,header\n1,2", b"\xff", (",".join(COLUMNS) + "\n").encode()]
)
def test_structural_failure_is_audited(prices_client, data):
    report = upload(prices_client, data).json()
    assert report["rejected"] == 1
    audit = prices_client.get(f"/api/v1/prices/imports/{report['id']}", headers=headers()).json()
    assert audit["items"][0]["row_number"] == 0


def test_admin_permission_is_server_controlled(prices_client, prices_app):
    assert prices_client.get("/api/v1/prices/access", headers=headers("farmer")).json() == {
        "can_import": False
    }
    assert prices_client.get("/api/v1/prices/access", headers=headers()).json() == {
        "can_import": True
    }
    assert (
        prices_client.post(
            "/api/v1/prices/imports?verified=true&verification_note=checked-source",
            headers=headers("farmer"),
            content=SAMPLE,
        ).status_code
        == 403
    )
    report = upload(prices_client).json()
    assert (
        prices_client.get(
            f"/api/v1/prices/imports/{report['id']}", headers=headers("farmer")
        ).status_code
        == 403
    )
    prices_app.state.settings.admin_user_ids = []
    assert upload(prices_client).status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "latest",
        "freshness",
        "access",
        "history?crop_id=tomato&market_id=dambulla-dec",
        "comparison?crop_id=tomato",
        f"imports/{ADMIN}",
    ],
)
def test_price_reads_require_auth(prices_client, path):
    assert prices_client.get("/api/v1/prices/" + path).status_code == 401


def test_upload_limits_and_attestation(prices_client):
    assert upload(prices_client, b"x" * 1048577).status_code == 413
    assert upload(prices_client, verified="false").status_code == 422
    assert upload(prices_client, verification_note="short").status_code == 422
    assert (
        prices_client.post(
            "/api/v1/prices/imports?verified=true&verification_note=checked-source",
            headers={"Authorization": "Bearer admin", "Content-Type": "application/json"},
            content=b"{}",
        ).status_code
        == 415
    )


@pytest.mark.parametrize(
    "path",
    [
        "latest?page=0",
        "latest?page_size=101",
        "latest?price_type=bad",
        "latest?crop_id=unknown",
        "comparison?crop_id=unknown",
        "history?crop_id=tomato&market_id=dambulla-dec&start_date=2025-12-30&end_date=2025-12-29",
        "history?crop_id=tomato&market_id=dambulla-dec&start_date=2020-01-01&end_date=2025-12-30",
    ],
)
def test_invalid_filters(prices_client, path):
    assert prices_client.get("/api/v1/prices/" + path, headers=headers()).status_code in (404, 422)


def test_empty_prices_and_outage_are_not_fabricated(prices_client, monkeypatch):
    assert prices_client.get("/api/v1/prices/latest", headers=headers()).json()["items"] == []
    summary = prices_client.get("/api/v1/prices/freshness", headers=headers()).json()
    assert summary["pairs_without_reported_price"] == 12 and summary["last_observed_at"] is None

    def unavailable(*args, **kwargs):
        raise OperationalError("private database", {}, Exception("secret"))

    monkeypatch.setattr(PriceRepository, "latest", unavailable)
    result = prices_client.get("/api/v1/prices/latest", headers=headers())
    assert result.status_code == 503 and "secret" not in result.text


def test_sample_provider_has_no_network_dependency():
    parsed = VerifiedCSVProvider().parse(SAMPLE)
    assert len(parsed) == 16 and all(row.data is not None for row in parsed)


def test_original_file_is_available_only_to_admin(prices_client):
    report = upload(prices_client).json()
    path = f"/api/v1/prices/imports/{report['id']}/file"
    response = prices_client.get(path, headers=headers())
    assert response.status_code == 200 and response.content == SAMPLE
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert prices_client.get(path, headers=headers("farmer")).status_code == 403
    assert prices_client.get(path).status_code == 401


def test_fresh_observation_and_inactive_reference(prices_client, prices_app):
    report = upload(prices_client, csv_bytes([row()])).json()
    assert report["accepted"] == 1
    assert (
        prices_client.get("/api/v1/prices/latest", headers=headers()).json()["items"][0][
            "freshness"
        ]
        == "fresh"
    )
    with prices_app.state.session_factory() as session:
        from app.models import Crop

        session.get(Crop, "tomato").active = False
        session.commit()
    assert prices_client.get("/api/v1/prices/latest", headers=headers()).json()["total"] == 0
    assert upload(prices_client, csv_bytes([row(price="123")])).json()["rejected"] == 1
