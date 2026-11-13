from uuid import UUID

from sqlalchemy.orm import Session

from app.models.farmer import FarmerProfile
from app.repositories.base import SQLAlchemyRepository


class FarmerProfileRepository(SQLAlchemyRepository[FarmerProfile]):
    def __init__(self, session: Session):
        super().__init__(session, FarmerProfile)

    def for_user(self, user_id: UUID) -> FarmerProfile | None:
        return self.session.get(FarmerProfile, user_id)

    def save(self, profile: FarmerProfile) -> None:
        self.session.add(profile)
        self.session.flush()
