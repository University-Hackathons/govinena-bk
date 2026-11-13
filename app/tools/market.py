from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from app.repositories.markets import MarketRepository, SyntheticMarketRepository
from app.schemas.agent import TransportEstimate
from app.schemas.market import MarketPrice


class MarketPriceTool(Protocol):
    def execute(self) -> list[MarketPrice]: ...


class RouteEstimationTool(Protocol):
    def execute(self, location: str, market_id: str) -> Decimal: ...


class TransportCostTool(Protocol):
    def execute(self, market_id: str, distance_km: Decimal) -> TransportEstimate: ...


class SyntheticMarketPriceTool:
    def __init__(self, repository: MarketRepository | None = None) -> None:
        self.repository = repository or SyntheticMarketRepository()

    def execute(self) -> list[MarketPrice]:
        return self.repository.list_prices()


class SyntheticRouteEstimationTool:
    def execute(self, location: str, market_id: str) -> Decimal:
        # Location is deliberately ignored: these are fixture distances, not map estimates.
        return {"demo-a": Decimal("10"), "demo-b": Decimal("40")}[market_id]


class SyntheticTransportCostTool:
    def execute(self, market_id: str, distance_km: Decimal) -> TransportEstimate:
        # Synthetic flat trip: LKR 300 plus LKR 20/km. Capacity is not modeled.
        cost = (Decimal("300") + distance_km * Decimal("20")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return TransportEstimate(market_id=market_id, distance_km=distance_km, cost=cost)
