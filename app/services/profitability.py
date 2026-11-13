from decimal import ROUND_HALF_UP, Decimal

from app.schemas.agent import ProfitabilityResult, TransportEstimate
from app.schemas.market import MarketPrice


class ProfitabilityCalculator:
    """Deterministic arithmetic; each monetary component uses half-up cents."""

    def calculate(
        self,
        price: MarketPrice,
        quantity_kg: Decimal,
        transport: TransportEstimate,
        production_cost: Decimal,
    ) -> ProfitabilityResult:
        if quantity_kg <= 0 or production_cost < 0:
            raise ValueError("Quantity must be positive and production cost non-negative")
        if price.market_id != transport.market_id:
            raise ValueError("Price and transport must refer to the same market")
        cents = Decimal("0.01")
        revenue = (price.price_per_kg * quantity_kg).quantize(cents, rounding=ROUND_HALF_UP)
        cost = transport.cost.quantize(cents, rounding=ROUND_HALF_UP)
        production = production_cost.quantize(cents, rounding=ROUND_HALF_UP)
        return ProfitabilityResult(
            market_id=price.market_id,
            revenue=revenue,
            transport_cost=cost,
            production_cost=production,
            estimated_profit=revenue - cost - production,
        )
