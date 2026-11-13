"""Repeatable, atomic, operator-only import: python -m app.db.seed_catalog."""

import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import build_engine, build_session_factory
from app.models.catalog import Crop, Market
from app.schemas.catalog import CropResponse, MarketResponse

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "catalog.json"


def load_seed(path: Path = DATA_PATH):
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = [
        (Crop, [CropResponse.model_validate(row) for row in raw["crops"]]),
        (Market, [MarketResponse.model_validate(row) for row in raw["markets"]]),
    ]
    for _, rows in records:
        if len({row.id for row in rows}) != len(rows):
            raise ValueError("Seed contains duplicate IDs")
    return records


def seed_catalog(session: Session, path: Path = DATA_PATH) -> dict[str, int]:
    # Validate the entire source before any writes. No network access during import.
    records = load_seed(path)
    dialect = session.get_bind().dialect.name
    insert = {"sqlite": sqlite_insert, "postgresql": postgres_insert}[dialect]
    counts = {}
    try:
        for model, rows in records:
            for row in rows:
                values = row.model_dump(mode="json")
                if model is Market:
                    values["coordinates_verified_on"] = row.coordinates_verified_on
                statement = insert(model).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=["id"],
                    set_={key: getattr(statement.excluded, key) for key in values if key != "id"},
                )
                session.execute(statement)
            counts[model.__tablename__] = len(rows)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return counts


def main():
    engine = build_engine(Settings())
    try:
        with build_session_factory(engine)() as session:
            print(seed_catalog(session))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
