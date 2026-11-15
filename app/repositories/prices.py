from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.catalog import Crop, Market
from app.models.prices import MarketPriceObservation as Observation
from app.models.prices import PriceImport, PriceImportRow


class PriceRepository:
    def __init__(self, session: Session):
        self.session = session

    def insert_once(self, model, values, key):
        insert = sqlite_insert if self.session.get_bind().dialect.name == "sqlite" else pg_insert
        return (
            self.session.execute(
                insert(model).values(**values).on_conflict_do_nothing(index_elements=[key])
            ).rowcount
            == 1
        )

    def latest_ids(self, filters):
        statement = (
            select(
                Observation.id,
                func.row_number()
                .over(
                    partition_by=[
                        Observation.crop_id,
                        Observation.market_id,
                        Observation.price_type,
                        Observation.quality_key,
                        Observation.source_key,
                    ],
                    order_by=[
                        Observation.observed_at.desc(),
                        Observation.imported_at.desc(),
                        Observation.id,
                    ],
                )
                .label("rank"),
            )
            .join(Crop)
            .join(Market)
            .where(Crop.active.is_(True), Market.active.is_(True))
        )
        for name, value in filters.items():
            if value is not None:
                statement = statement.where(getattr(Observation, name) == value)
        ranked = statement.subquery()
        return select(ranked.c.id).where(ranked.c.rank == 1)

    @staticmethod
    def records():
        return (
            select(Observation, Crop.name_en.label("crop_name"), Market.name.label("market_name"))
            .join(Crop)
            .join(Market)
        )

    def latest(self, filters, page, page_size):
        statement = self.records().where(Observation.id.in_(self.latest_ids(filters)))
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.execute(
            statement.order_by(
                Crop.name_en, Market.name, Observation.quality_key, Observation.source_key
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return rows, total

    def history(self, filters, start, end, limit):
        statement = self.records().where(
            Observation.observed_at >= start, Observation.observed_at < end
        )
        for name, value in filters.items():
            if value is not None:
                statement = statement.where(getattr(Observation, name) == value)
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.execute(
            statement.order_by(
                Observation.observed_at,
                Observation.source_key,
                Observation.quality_key,
                Observation.id,
            ).limit(limit)
        ).all()
        return rows, total

    def import_report(self, id):
        return self.session.get(PriceImport, id)

    def import_rows(self, id, page, page_size):
        statement = select(PriceImportRow).where(PriceImportRow.import_id == id)
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        return self.session.scalars(
            statement.order_by(PriceImportRow.row_number)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all(), total
