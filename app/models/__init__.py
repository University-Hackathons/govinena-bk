"""Import future ORM models here so Alembic discovers their metadata."""

from app.db.base import Base

__all__ = ["Base"]
