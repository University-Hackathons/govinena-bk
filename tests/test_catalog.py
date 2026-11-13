import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.auth import Identity, active_identity
from app.db.seed_catalog import DATA_PATH, load_seed, seed_catalog
from app.models import Base, Crop, Market
from app.repositories.catalog import CatalogRepository
from app.schemas.catalog import MarketResponse
from app.services.catalog import CatalogService


@pytest.fixture
def catalog_app(application):
    Base.metadata.create_all(application.state.engine)
    application.dependency_overrides[active_identity] = lambda: Identity(
        UUID(int=1), UUID(int=2), "catalog@example.test"
    )
    with application.state.session_factory() as session:
        seed_catalog(session)
    return application


@pytest.fixture
def catalog_client(catalog_app):
    with TestClient(catalog_app) as client:
        yield client


def test_seed_is_repeatable_updates_and_preserves_unlisted_records(catalog_app):
    with catalog_app.state.session_factory() as session:
        assert seed_catalog(session) == {"crops": 4, "markets": 3}
        session.get(Crop, "tomato").name_en = "Old label"
        session.commit()
        seed_catalog(session)
        assert session.get(Crop, "tomato").name_en == "Tomato"
        assert session.scalar(select(func.count()).select_from(Crop)) == 4
        assert session.scalar(select(func.count()).select_from(Market)) == 3
        crops, total = CatalogRepository(session).list(Crop)
        assert total == 4 and len({crop.id for crop in crops}) == 4
        assert all(crop.default_unit == "kg" and crop.name_si and crop.name_ta for crop in crops)
        # Operator additions are not removed by a subsequent seed import.
        values = load_seed()[0][1][0].model_dump(mode="json")
        session.add(
            Crop(**(values | {"id": "test-operator-crop", "name_en": "Test operator crop"}))
        )
        session.commit()
        seed_catalog(session)
        assert session.get(Crop, "test-operator-crop") is not None
        assert session.scalar(select(func.count()).select_from(Crop)) == 5


def test_seed_requires_unique_ids_before_any_writes(catalog_app, tmp_path):
    data = json.loads(DATA_PATH.read_text())
    data["crops"].append(data["crops"][0])
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    with catalog_app.state.session_factory() as session, pytest.raises(ValueError):
        seed_catalog(session, path)


def test_seed_transaction_rolls_back_conflicting_names(catalog_app, tmp_path):
    data = json.loads(DATA_PATH.read_text())
    data["crops"][0]["name_en"] = "Changed tomato"
    data["markets"][1]["name"] = data["markets"][0]["name"]
    path = tmp_path / "conflict.json"
    path.write_text(json.dumps(data))
    with catalog_app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            seed_catalog(session, path)
        assert session.get(Crop, "tomato").name_en == "Tomato"


@pytest.mark.parametrize(
    "path,total,first_id",
    [
        ("crops?q=TOmA", 1, "tomato"),
        ("crops?q=තක්කාලි", 1, "tomato"),
        ("crops?q=கத்தரி", 1, "brinjal"),
        ("crops?category=root_vegetable", 1, "carrot"),
        ("crops?category=vegetable", 3, "brinjal"),
        ("markets?district=Matale", 1, "dambulla-dec"),
        ("markets?q=MEegoda", 1, "meegoda-dec"),
        ("markets?q=Colombo&market_type=dedicated_economic_centre", 1, "meegoda-dec"),
        ("markets?market_type=wholesale_market", 0, None),
        ("markets?district=Matale&q=meegoda", 0, None),
        ("crops?q=%", 0, None),
        ("crops?q=_", 0, None),
        ("markets?q=not-found", 0, None),
        ("crops?active=false", 0, None),
    ],
)
def test_search_and_filters(catalog_client, path, total, first_id):
    response = catalog_client.get("/api/v1/" + path)
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == total
    assert result["page"] == 1
    if first_id:
        assert result["items"][0]["id"] == first_id
    else:
        assert result["items"] == []


def test_pagination_is_stable_and_bounded(catalog_client):
    first = catalog_client.get("/api/v1/crops?page=1&page_size=2").json()
    second = catalog_client.get("/api/v1/crops?page=2&page_size=2").json()
    assert first["total"] == second["total"] == 4
    assert {row["id"] for row in first["items"]}.isdisjoint(row["id"] for row in second["items"])
    assert catalog_client.get("/api/v1/crops?page=99").json()["items"] == []
    assert catalog_client.get("/api/v1/markets?page_size=1").json()["total"] == 3


@pytest.mark.parametrize("kind,id", [("crops", "tomato"), ("markets", "dambulla-dec")])
def test_detail_unknown_and_read_only(catalog_client, kind, id):
    response = catalog_client.get(f"/api/v1/{kind}/{id}")
    assert response.status_code == 200
    record = response.json()
    assert record["id"] == id and record["sources"]
    assert "price_per_kg" not in record
    assert catalog_client.get(f"/api/v1/{kind}/unknown").status_code == 404
    for method in ("post", "put", "patch", "delete"):
        assert getattr(catalog_client, method)(f"/api/v1/{kind}/{id}").status_code == 405
    assert catalog_client.post(f"/api/v1/{kind}", json={}).status_code == 405


@pytest.mark.parametrize("path", ["crops", "crops/tomato", "markets", "markets/dambulla-dec"])
def test_catalog_requires_authentication(client, path):
    assert client.get("/api/v1/" + path).status_code == 401


@pytest.mark.parametrize(
    "query", ["page=0", "page=-1", "page_size=101", "page_size=0", "active=maybe", "q=" + "a" * 101]
)
def test_invalid_common_filters(catalog_client, query):
    for kind in ("crops", "markets"):
        assert catalog_client.get(f"/api/v1/{kind}?{query}").status_code == 422


@pytest.mark.parametrize(
    "path", ["crops?category=invalid", "markets?district=invalid", "markets?market_type=invalid"]
)
def test_invalid_specific_filters(catalog_client, path):
    assert catalog_client.get("/api/v1/" + path).status_code == 422


def test_empty_unseeded_database_and_inactive(catalog_app):
    with catalog_app.state.session_factory() as session:
        session.get(Crop, "tomato").active = False
        session.commit()
        service = CatalogService(CatalogRepository(session))
        assert service.list(Crop, active=False, page=1, page_size=12).total == 1
        session.query(Crop).delete()
        session.commit()
        assert service.list(Crop, page=1, page_size=12).items == []


def test_market_provenance_and_coordinate_validation(catalog_app):
    rows = load_seed()[1][1]
    for row in rows:
        assert row.latitude is None and row.longitude is None
        assert row.coordinate_source is None and row.coordinates_verified_on is None
        assert all(str(source.url).startswith("https://") for source in row.sources)
    base = rows[0].model_dump()
    for invalid in [
        {"latitude": 7},
        {"latitude": 91, "longitude": 80},
        {"latitude": 7, "longitude": -181},
        {"latitude": float("nan")},
        {"latitude": 7, "longitude": 80},
        {"coordinate_source": "https://example.org"},
    ]:
        with pytest.raises(ValidationError):
            MarketResponse.model_validate(base | invalid)
    valid = base | {
        "latitude": 7,
        "longitude": 80,
        "coordinate_source": "https://example.org/verified",
        "coordinates_verified_on": "2026-11-13",
    }
    assert MarketResponse.model_validate(valid).latitude == 7
    with catalog_app.state.session_factory() as session:
        market = session.get(Market, "dambulla-dec")
        market.latitude = 7
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        market.latitude, market.longitude = 91, 80
        market.coordinate_source = "https://example.org/verified"
        market.coordinates_verified_on = rows[0].sources[0].accessed_on
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_storage_outage_is_safe(catalog_client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OperationalError("secret connection", {}, Exception("private"))

    monkeypatch.setattr(CatalogRepository, "list", unavailable)
    monkeypatch.setattr(CatalogRepository, "get", unavailable)
    for path in ("crops", "markets", "crops/tomato", "markets/dambulla-dec"):
        response = catalog_client.get("/api/v1/" + path)
        assert response.status_code == 503
        assert "private" not in response.text and "secret" not in response.text
