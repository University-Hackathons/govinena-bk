from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.db.session import SessionDependency
from app.models.catalog import Market
from app.repositories.catalog import CatalogRepository
from app.schemas.catalog import CatalogID, MarketResponse, MarketType, PageResponse
from app.schemas.farmer import District
from app.services.catalog import CatalogService

router = APIRouter(tags=["markets"])


def get_catalog_service(session: SessionDependency) -> CatalogService:
    return CatalogService(CatalogRepository(session))


CatalogServiceDependency = Annotated[CatalogService, Depends(get_catalog_service)]


@router.get("/markets", response_model=PageResponse[MarketResponse])
def list_markets(
    service: CatalogServiceDependency,
    q: Annotated[str, Query(max_length=100)] = "",
    district: District | None = None,
    market_type: MarketType | None = None,
    active: bool = True,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 12,
):
    return service.list(
        Market,
        q=q,
        district=district,
        market_type=market_type,
        active=active,
        page=page,
        page_size=page_size,
    )


@router.get("/markets/{id}", response_model=MarketResponse)
def get_market(id: CatalogID, service: CatalogServiceDependency):
    return service.get(Market, id)
