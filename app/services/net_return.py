"""Pure Decimal financial arithmetic, without routing, storage or AI dependencies."""

from decimal import ROUND_HALF_UP, Decimal


def money(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("Money must be finite")
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def gross_revenue(quantity_kg: Decimal, price_per_kg: Decimal) -> Decimal:
    if quantity_kg < 0 or price_per_kg < 0:
        raise ValueError("Quantity and price cannot be negative")
    return money(quantity_kg * price_per_kg)


def total_cost(transport: Decimal, market: Decimal) -> Decimal:
    if transport < 0 or market < 0:
        raise ValueError("Costs cannot be negative")
    return money(transport) + money(market)


def net_proceeds(gross: Decimal, costs: Decimal) -> Decimal:
    if gross < 0 or costs < 0:
        raise ValueError("Gross and costs cannot be negative")
    return money(gross) - money(costs)
