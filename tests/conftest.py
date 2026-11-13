import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def application():
    return create_app(
        Settings(_env_file=None, environment="test", database_url="sqlite:///:memory:")
    )


@pytest.fixture
def client(application):
    with TestClient(application) as test_client:
        yield test_client
