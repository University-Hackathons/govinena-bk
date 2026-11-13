import json
import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter


def test_settings_environment_overrides_dotenv(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        'GOVINENA_APP_NAME=From file\nGOVINENA_CORS_ORIGINS=["http://localhost:4000"]\n'
    )
    monkeypatch.setenv("GOVINENA_APP_NAME", "From environment")
    settings = Settings(_env_file=dotenv)
    assert settings.app_name == "From environment"
    assert str(settings.cors_origins[0]) == "http://localhost:4000/"


@pytest.mark.parametrize(
    "origin", ["*", "http://localhost:3000/path", "http://user:secret@localhost"]
)
def test_settings_reject_invalid_origins(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=[origin])


def test_json_formatter_omits_arbitrary_extras_and_tracebacks():
    record = logging.LogRecord("app", logging.ERROR, __file__, 1, "Request failed", (), None)
    record.authorization = "secret-value"
    record.exc_text = "secret-value"
    record.request_id = "123"
    output = JsonFormatter().format(record)
    assert json.loads(output)["request_id"] == "123"
    assert "secret-value" not in output
