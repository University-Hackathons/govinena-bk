from app.repositories.markets import MarketRepository
from app.schemas.market import MarketListResponse


class MarketService:
    def __init__(self, repository: MarketRepository) -> None:
        self.repository = repository

    def list_demo_markets(self) -> MarketListResponse:
        return MarketListResponse(markets=self.repository.list_prices())
