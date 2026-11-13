from decimal import Decimal
from typing import Protocol

from app.schemas.market import MarketPrice


class MarketRepository(Protocol):
    def list_prices(self) -> list[MarketPrice]: ...


class SyntheticMarketRepository:
    """Explicit fixtures, independent of the database and any real market feed."""

    def list_prices(self) -> list[MarketPrice]:
        return [
            MarketPrice(market_id="demo-a", name="Synthetic Market A", price_per_kg=Decimal("120")),
            MarketPrice(market_id="demo-b", name="Synthetic Market B", price_per_kg=Decimal("135")),
        ]
