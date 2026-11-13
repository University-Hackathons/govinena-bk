from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

SYNTHETIC_NOTICE = "Synthetic test data only. Not real prices, routes, or farming advice."


class MarketPrice(BaseModel):
    market_id: str
    name: str
    crop: Literal["tomato"] = "tomato"
    price_per_kg: Decimal = Field(gt=0)
    currency: Literal["LKR"] = "LKR"
    data_source: Literal["synthetic"] = "synthetic"


class MarketListResponse(BaseModel):
    data_source: Literal["synthetic"] = "synthetic"
    notice: str = SYNTHETIC_NOTICE
    markets: list[MarketPrice]
