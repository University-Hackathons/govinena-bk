from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.schemas.logistics import CostBreakdown, RouteResult, Tariff


class LogisticsValidationError(ValueError):
    def __init__(self, field: str, message: str):
        self.field, self.message = field, message


def loads(quantity: Decimal, capacity: Decimal | None, allow_multiple: bool):
    if capacity is None or capacity <= 0:
        raise LogisticsValidationError(
            "vehicle_capacity_kg", "Enter a positive vehicle capacity before estimating transport."
        )
    trips = int((quantity / capacity).to_integral_value(rounding=ROUND_CEILING))
    if trips > 100:
        raise LogisticsValidationError(
            "vehicle_capacity_kg",
            "This load needs over 100 trips. Use a more suitable vehicle capacity.",
        )
    if trips > 1 and not allow_multiple:
        raise LogisticsValidationError(
            "allow_multiple_trips",
            "The load exceeds vehicle capacity. Choose a larger vehicle "
            "or explicitly allow multiple trips.",
        )
    remaining = quantity
    trip_loads = []
    for _ in range(trips):
        trip_loads.append(min(remaining, capacity))
        remaining -= trip_loads[-1]
    return trips, trip_loads


def calculate_cost(
    route: RouteResult, tariff: Tariff, basis: str, trips: int
) -> CostBreakdown | None:
    if route.distance_km is None or tariff.rate_per_km_lkr is None:
        return None
    distance = route.distance_km
    if basis == "round_trip":
        if route.return_distance_km is None:
            return None
        distance += route.return_distance_km
    billable = distance * trips

    def rounded(value):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    components = {
        name: rounded(getattr(tariff, name) * trips)
        for name in (
            "base_charge_lkr",
            "loading_lkr",
            "unloading_lkr",
            "toll_lkr",
            "market_handling_lkr",
        )
    }
    distance_charge = rounded(billable * tariff.rate_per_km_lkr)
    total = sum(components.values(), Decimal(0)) + distance_charge
    if total > Decimal("1000000000000"):
        raise LogisticsValidationError(
            "tariff", "The total exceeds the supported LKR limit. Check the rates and charges."
        )
    return CostBreakdown(
        **components,
        distance_charge_lkr=distance_charge,
        billable_distance_km=billable,
        calculated_total_lkr=total,
    )
