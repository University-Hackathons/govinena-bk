from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models.catalog import Crop
from app.models.produce import ProduceBatch


class ProduceRepository:
    def __init__(self, session: Session):
        self.session = session

    def expire_due(self, farmer_id: UUID, now: datetime):
        return self.session.execute(
            update(ProduceBatch)
            .where(
                ProduceBatch.farmer_id == farmer_id,
                ProduceBatch.status.in_(["available", "reserved"]),
                ProduceBatch.sell_by <= now,
            )
            .values(status="expired", updated_at=now)
            .execution_options(synchronize_session=False)
        ).rowcount

    def get(self, farmer_id: UUID, batch_id: UUID, *, lock=False) -> ProduceBatch | None:
        statement = (
            select(ProduceBatch)
            .where(ProduceBatch.id == batch_id, ProduceBatch.farmer_id == farmer_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def list(self, farmer_id: UUID, *, q: str, status: str | None, page: int, page_size: int):
        statement = select(ProduceBatch).join(Crop).where(ProduceBatch.farmer_id == farmer_id)
        statement = statement.where(
            ProduceBatch.status == status if status else ProduceBatch.status != "archived"
        )
        if q.strip():
            statement = statement.where(
                or_(
                    *(
                        func.lower(column).contains(q.strip().lower(), autoescape=True)
                        for column in [
                            Crop.name_en,
                            Crop.name_si,
                            Crop.name_ta,
                            ProduceBatch.quality,
                            ProduceBatch.source_town,
                            ProduceBatch.source_district,
                            ProduceBatch.notes,
                        ]
                    )
                )
            )
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        items = self.session.scalars(
            statement.order_by(ProduceBatch.created_at.desc(), ProduceBatch.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .execution_options(populate_existing=True)
        ).all()
        return items, total
