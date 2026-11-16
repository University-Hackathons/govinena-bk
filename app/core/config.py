"""Validated configuration. Environment variables take precedence over .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GOVINENA_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    app_name: str = Field(default="GoviNena API", min_length=1)
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr = Field(
        default=SecretStr(f"sqlite:///{PROJECT_ROOT / 'govinena.db'}"),
        validation_alias="DATABASE_URL",
        repr=False,
    )
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENAI_API_KEY", repr=False
    )
    openai_model: str | None = Field(default=None, validation_alias="OPENAI_MODEL")
    reasoning_mode: Literal["mock", "openai"] = "mock"
    cors_origins: list[HttpUrl] = Field(default_factory=lambda: [HttpUrl("http://localhost:3000")])

    supabase_url: HttpUrl | None = None
    supabase_publishable_key: SecretStr | None = Field(default=None, repr=False)
    supabase_audience: str = "authenticated"
    admin_user_ids: list[UUID] = Field(default_factory=list)
    map_provider: Literal["manual", "mapbox"] = Field(
        default="manual", validation_alias="MAP_PROVIDER"
    )
    mapbox_access_token: SecretStr | None = Field(
        default=None, validation_alias="MAPBOX_ACCESS_TOKEN", repr=False
    )
    routing_allow_mapbox_cache: bool = False
    route_cache_ttl_seconds: int = Field(default=300, ge=0, le=3600)

    agent_max_steps: int = Field(default=40, ge=12, le=60)
    agent_max_attempts: int = Field(default=2, ge=1, le=3)
    agent_timeout_seconds: int = Field(default=180, ge=30, le=300)

    price_stale_after_hours: int = Field(default=72, ge=1, le=720)

    @field_validator(
        "supabase_url", "supabase_publishable_key", "mapbox_access_token", mode="before"
    )
    @classmethod
    def empty_auth_setting(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_database(self) -> "Settings":
        try:
            url = make_url(self.database_url.get_secret_value())
        except Exception:
            raise ValueError("DATABASE_URL must be a valid database URL") from None
        if url.drivername not in (
            "sqlite",
            "sqlite+pysqlite",
            "postgres",
            "postgresql",
            "postgresql+psycopg",
        ):
            raise ValueError("Only synchronous SQLite and PostgreSQL with psycopg are supported")
        if self.environment == "production" and not url.drivername.startswith("postgres"):
            raise ValueError("Production requires PostgreSQL")
        return self

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, origins: list[HttpUrl]) -> list[HttpUrl]:
        for origin in origins:
            if (
                origin.username is not None
                or origin.password is not None
                or origin.path not in (None, "/")
                or origin.query is not None
                or origin.fragment is not None
            ):
                raise ValueError("CORS origins must contain only scheme, host, and optional port")
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
