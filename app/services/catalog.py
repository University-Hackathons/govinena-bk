from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.models.catalog import Crop
from app.repositories.catalog import CatalogRepository
from app.schemas.catalog import CropResponse, MarketResponse, PageResponse


class CatalogService:
    def __init__(self, repository: CatalogRepository):
        self.repository = repository

    @staticmethod
    def schema(model):
        return CropResponse if model is Crop else MarketResponse

    def list(self, model, **filters):
        try:
            items, total = self.repository.list(model, **filters)
        except SQLAlchemyError:
            raise HTTPException(503) from None
        schema = self.schema(model)
        return PageResponse[schema](
            items=[schema.model_validate(item) for item in items],
            total=total,
            page=filters["page"],
            page_size=filters["page_size"],
        )

    def get(self, model, identifier):
        try:
            record = self.repository.get(model, identifier)
        except SQLAlchemyError:
            raise HTTPException(503) from None
        if record is None:
            raise HTTPException(404)
        return self.schema(model).model_validate(record)
