from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.logistics import LogisticsEstimate
from app.models.produce import ProduceBatch


class LogisticsRepository:
    def __init__(self, session: Session):
        self.session = session

    def batch(self, farmer_id: UUID, batch_id: UUID):
        return self.session.scalar(
            select(ProduceBatch).where(
                ProduceBatch.id == batch_id, ProduceBatch.farmer_id == farmer_id
            )
        )

    def save(self, record: LogisticsEstimate):
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)

    def history(self, farmer_id: UUID, batch_id: UUID):
        return self.session.scalars(
            select(LogisticsEstimate)
            .where(
                LogisticsEstimate.farmer_id == farmer_id,
                LogisticsEstimate.produce_batch_id == batch_id,
            )
            .order_by(LogisticsEstimate.created_at.desc(), LogisticsEstimate.id)
            .limit(20)
        ).all()
