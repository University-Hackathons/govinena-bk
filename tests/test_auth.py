import base64
import json
import time
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models import Base

ISSUER = "https://auth.example.test/auth/v1"
USER_A = str(UUID(int=1))
USER_B = str(UUID(int=2))
SESSION_A = str(UUID(int=3))
SESSION_B = str(UUID(int=4))


def token_for(user=USER_A, session=SESSION_A, **changes):
    claims = {
        "sub": user,
        "session_id": session,
        "iss": ISSUER,
        "aud": "authenticated",
        "exp": time.time() + 300,
        "role": "authenticated",
    } | changes
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.provider-verified-signature"


@pytest.fixture
def auth_client(monkeypatch):
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            database_url="sqlite:///:memory:",
            supabase_url="https://auth.example.test",
            supabase_publishable_key="test-public-key",
        )
    )
    Base.metadata.create_all(app.state.engine)
    provider = {"status": 200, "confirmed": True, "unavailable": False, "requests": []}

    def handler(request):
        provider["requests"].append(request)
        if provider["unavailable"]:
            raise httpx.ConnectError("private details", request=request)
        if provider["status"] != 200:
            return httpx.Response(provider["status"])
        token = request.headers["authorization"].split(" ")[1]
        try:
            claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
            user = claims["sub"]
            if not token.endswith(".provider-verified-signature"):
                return httpx.Response(401)
        except Exception:
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={
                "id": user,
                "email": f"{user}@example.test",
                "email_confirmed_at": "2026-11-13T00:00:00Z" if provider["confirmed"] else None,
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.core.auth.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    with TestClient(app) as client:
        yield client, provider


def headers(token=None):
    return {"Authorization": f"Bearer {token or token_for()}"}


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/v1/users/me", "get"),
        ("/api/v1/farmer/profile", "get"),
        ("/api/v1/farmer/profile", "put"),
        ("/api/v1/markets", "get"),
        ("/api/v1/agent/demo", "post"),
        ("/api/v1/auth/logout", "post"),
    ],
)
def test_missing_credentials(auth_client, path, method):
    client, provider = auth_client
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["message"] == "Unauthorized"
    assert not provider["requests"]


@pytest.mark.parametrize("credentials", ["Basic abc", "Bearer invalid", "Bearer "])
def test_malformed_credentials(auth_client, credentials):
    client, _ = auth_client
    assert client.get("/api/v1/users/me", headers={"Authorization": credentials}).status_code == 401


@pytest.mark.parametrize(
    "changes",
    [
        {"exp": 1},
        {"iss": "https://attacker.test/auth/v1"},
        {"aud": "wrong"},
        {"session_id": "invalid"},
        {"nbf": 9999999999},
        {"exp": "invalid"},
    ],
)
def test_invalid_claims(auth_client, changes):
    client, _ = auth_client
    assert client.get("/api/v1/users/me", headers=headers(token_for(**changes))).status_code == 401


def test_provider_rejects_bad_signature(auth_client):
    client, _ = auth_client
    response = client.get("/api/v1/users/me", headers=headers(token_for() + "tampered"))
    assert response.status_code == 401


@pytest.mark.parametrize("status,expected", [(401, 401), (403, 401), (429, 503), (500, 503)])
def test_provider_errors(auth_client, status, expected):
    client, provider = auth_client
    provider["status"] = status
    assert client.get("/api/v1/users/me", headers=headers()).status_code == expected


def test_provider_unavailable(auth_client):
    client, provider = auth_client
    provider["unavailable"] = True
    response = client.get("/api/v1/users/me", headers=headers())
    assert response.status_code == 503
    assert "private" not in response.text


def test_unconfirmed_and_wrong_role_are_forbidden(auth_client):
    client, provider = auth_client
    provider["confirmed"] = False
    assert client.get("/api/v1/users/me", headers=headers()).status_code == 403
    provider["confirmed"] = True
    assert (
        client.get("/api/v1/users/me", headers=headers(token_for(role="anon"))).status_code == 403
    )


def test_profile_provisioning_refresh_and_isolation(auth_client):
    client, provider = auth_client
    a = client.get("/api/v1/users/me", headers=headers())
    assert a.status_code == 200
    assert a.headers["cache-control"] == "no-store"
    assert a.json()["id"] == USER_A
    assert set(a.json()) == {"id", "email", "created_at"}
    refreshed = client.get("/api/v1/users/me", headers=headers())
    assert a.json() == refreshed.json()
    b = client.get("/api/v1/users/me", headers=headers(token_for(USER_B, SESSION_B)))
    assert b.status_code == 200
    assert b.json()["id"] == USER_B
    # No caller-selected user route exists; supplied IDs cannot change ownership.
    assert client.get(f"/api/v1/users/{USER_B}", headers=headers()).status_code == 404
    query = client.get(f"/api/v1/users/me?user_id={USER_B}", headers=headers())
    assert query.json() == a.json()
    request = provider["requests"][0]
    assert str(request.url) == ISSUER + "/user"
    assert request.headers["apikey"] == "test-public-key"


def test_logout_revokes_only_current_session_and_survives_refresh(auth_client):
    client, _ = auth_client
    assert client.get("/api/v1/users/me", headers=headers()).status_code == 200
    for _ in range(2):
        assert client.post("/api/v1/auth/logout", headers=headers()).status_code == 204
    assert client.get("/api/v1/users/me", headers=headers()).status_code == 401
    refreshed = token_for(exp=time.time() + 600)
    assert client.get("/api/v1/users/me", headers=headers(refreshed)).status_code == 401
    assert (
        client.get("/api/v1/users/me", headers=headers(token_for(USER_B, SESSION_B))).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/users/me", headers=headers(token_for(session=str(UUID(int=5))))
        ).status_code
        == 200
    )


def test_missing_configuration_fails_closed(client):
    response = client.get("/api/v1/users/me", headers=headers())
    assert response.status_code == 503


def test_farmer_profile_provider_outage_fails_closed(auth_client):
    client, provider = auth_client
    provider["unavailable"] = True
    assert client.get("/api/v1/farmer/profile", headers=headers()).status_code == 503
    response = client.put(
        "/api/v1/farmer/profile",
        headers=headers(),
        json={
            "full_name": "Test Farmer",
            "district": "Kandy",
            "town": "Gampola",
            "preferred_language": "en",
            "transport_mode": "other",
        },
    )
    assert response.status_code == 503
    assert "private" not in response.text
