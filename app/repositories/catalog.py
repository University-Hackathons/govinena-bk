from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.catalog import Crop, Market


class CatalogRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self, model, *, q="", page=1, page_size=12, active=True, **filters):
        columns = (
            [Crop.name_en, Crop.name_si, Crop.name_ta]
            if model is Crop
            else [Market.name, Market.district, Market.location]
        )
        statement = select(model).where(model.active == active)
        if q.strip():
            # contains(autoescape) treats % and _ as text, not user-supplied wildcards.
            statement = statement.where(
                or_(
                    *(
                        func.lower(column).contains(q.strip().lower(), autoescape=True)
                        for column in columns
                    )
                )
            )
        for name, value in filters.items():
            if value is not None:
                statement = statement.where(getattr(model, name) == value)
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        order = Crop.name_en if model is Crop else Market.name
        items = self.session.scalars(
            statement.order_by(order, model.id).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return items, total

    def get(self, model, identifier):
        return self.session.get(model, identifier)
