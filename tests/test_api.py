import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field


@pytest.mark.parametrize("path", ["/health", "/api/v1/health"])
def test_health(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response.headers["x-request-id"]) == 32


def test_docs_and_openapi(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert {"/health", "/api/v1/health"} <= schema["paths"].keys()


@pytest.mark.parametrize("method,path,status", [("get", "/missing", 404), ("post", "/health", 405)])
def test_http_errors(client, method, path, status):
    response = getattr(client, method)(path)
    assert response.status_code == status
    assert response.json()["error"]["code"] == "http_error"
    if status == 405:
        assert "GET" in response.headers["allow"]


def test_cors(client):
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    rejected = client.get("/health", headers={"Origin": "https://untrusted.example"})
    assert "access-control-allow-origin" not in rejected.headers


class ExampleRequest(BaseModel):
    quantity: int = Field(gt=0)


def test_validation_errors_do_not_echo_inputs(application):
    @application.post("/test-validation")
    async def validate(payload: ExampleRequest):
        return payload

    with TestClient(application) as client:
        response = client.post("/test-validation", json={"quantity": "secret-value"})
        assert response.status_code == 422
        assert response.json() == {
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": [{"location": ["body", "quantity"], "type": "int_parsing"}],
            }
        }
        malformed = client.post(
            "/test-validation", content='{"quantity":', headers={"Content-Type": "application/json"}
        )
        assert malformed.status_code == 422
        assert malformed.json()["error"]["code"] == "validation_error"


def test_unexpected_errors_are_safe_and_support_cors(application, capsys):
    @application.get("/test-failure")
    async def failure():
        raise RuntimeError("secret-value")

    with TestClient(application) as client:
        response = client.get(
            "/test-failure?token=secret-value",
            headers={"Origin": "http://localhost:3000", "Authorization": "Bearer secret-value"},
        )
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_server_error",
            "message": "Internal server error",
            "details": [],
        }
    }
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    logs = capsys.readouterr().err
    assert '"error_type": "RuntimeError"' in logs
    assert "secret-value" not in logs


def test_http_exception_details_are_private(application):
    @application.get("/test-forbidden")
    async def forbidden():
        raise HTTPException(403, detail="secret-value")

    with TestClient(application) as client:
        response = client.get("/test-forbidden")
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Forbidden"
