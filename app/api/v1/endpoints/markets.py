from typing import Annotated

from fastapi import APIRouter, Depends

from app.repositories.markets import SyntheticMarketRepository
from app.schemas.market import MarketListResponse
from app.services.markets import MarketService

router = APIRouter(tags=["markets"])


def get_market_service() -> MarketService:
    return MarketService(SyntheticMarketRepository())


@router.get("/markets", response_model=MarketListResponse)
def list_markets(service: Annotated[MarketService, Depends(get_market_service)]):
    """Return synthetic fixtures only; no live market feed or database query."""
    return service.list_demo_markets()
