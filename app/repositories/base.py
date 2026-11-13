from sqlalchemy.orm import Session

from app.db.base import Base


class SQLAlchemyRepository[Model: Base]:
    """Small persistence foundation; endpoints should call services/repositories."""

    def __init__(self, session: Session, model: type[Model]) -> None:
        self.session = session
        self.model = model

    def get(self, identifier: int) -> Model | None:
        return self.session.get(self.model, identifier)
