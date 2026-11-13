from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.endpoints.markets import CatalogServiceDependency
from app.core.auth import active_identity
from app.models.catalog import Crop
from app.schemas.catalog import CatalogID, Category, CropResponse, PageResponse

router = APIRouter(tags=["crops"], dependencies=[Depends(active_identity)])


@router.get("/crops", response_model=PageResponse[CropResponse])
def list_crops(
    service: CatalogServiceDependency,
    q: Annotated[str, Query(max_length=100)] = "",
    category: Category | None = None,
    active: bool = True,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 12,
):
    return service.list(Crop, q=q, category=category, active=active, page=page, page_size=page_size)


@router.get("/crops/{id}", response_model=CropResponse)
def get_crop(id: CatalogID, service: CatalogServiceDependency):
    return service.get(Crop, id)
