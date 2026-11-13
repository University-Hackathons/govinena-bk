from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import inspect, text
from starlette.requests import Request

from app.core.config import PROJECT_ROOT, Settings
from app.db.session import build_engine, build_session_factory, get_session


@pytest.mark.parametrize("prefix", ["postgresql", "postgres", "postgresql+psycopg"])
def test_postgres_driver_without_connecting(prefix):
    settings = Settings(_env_file=None, database_url=f"{prefix}://user:secret@localhost/db")
    engine = build_engine(settings)
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"
    assert engine.hide_parameters
    assert "secret" not in repr(settings)
    engine.dispose()


def test_database_url_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    assert Settings(_env_file=None).database_url.get_secret_value() == "sqlite:///:memory:"


@pytest.mark.parametrize("url", ["not-a-url", "mysql://localhost/db"])
def test_invalid_database_url(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=url)


def test_production_requires_postgres():
    with pytest.raises(ValidationError, match="Production requires PostgreSQL"):
        Settings(_env_file=None, environment="production", database_url="sqlite:///:memory:")


def test_sqlite_sessions_commit_and_rollback():
    engine = build_engine(Settings(_env_file=None, database_url="sqlite:///:memory:"))
    factory = build_session_factory(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE probe (value INTEGER)"))
    app = SimpleNamespace(state=SimpleNamespace(session_factory=factory))
    request = Request({"type": "http", "app": app})
    dependency = get_session(request)
    session = next(dependency)
    session.execute(text("INSERT INTO probe VALUES (1)"))
    session.commit()
    with pytest.raises(StopIteration):
        next(dependency)
    dependency = get_session(request)
    session = next(dependency)
    session.execute(text("INSERT INTO probe VALUES (2)"))
    with pytest.raises(RuntimeError):
        dependency.throw(RuntimeError("simulate a failed service operation"))
    with factory() as session:
        assert session.scalar(text("SELECT count(*) FROM probe")) == 1
    engine.dispose()


def test_migrations_upgrade_downgrade_and_check(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    command.check(config)
    engine = build_engine(Settings(_env_file=None, database_url=url))
    assert inspect(engine).get_table_names() == [
        "alembic_version",
        "crops",
        "farmer_profiles",
        "markets",
        "revoked_sessions",
        "users",
    ]
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM alembic_version")) == 1
    command.downgrade(config, "base")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM alembic_version")) == 0
    engine.dispose()
