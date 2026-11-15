"""Local, manually verified CSV provider. Never fetches or scrapes source URLs."""

import csv
import io
from dataclasses import dataclass
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from pydantic import ValidationError

from app.schemas.prices import SL, PriceInput

COLUMNS = [
    "crop_id",
    "market_id",
    "observation_date",
    "observed_at",
    "price",
    "unit",
    "unit_quantity",
    "currency",
    "price_type",
    "quality",
    "source_name",
    "source_url",
    "document_reference",
    "source_publication_date",
    "source_published_at",
]
MAX_BYTES = 1048576
MAX_ROWS = 2000


@dataclass
class ParsedRow:
    number: int
    raw: dict
    data: PriceInput | None
    error: str | None = None


class MarketPriceProvider(Protocol):
    def parse(self, content: bytes) -> list[ParsedRow]: ...


class InvalidCSV(ValueError):
    pass


class VerifiedCSVProvider:
    def parse(self, content: bytes) -> list[ParsedRow]:
        if not content or len(content) > MAX_BYTES:
            raise InvalidCSV("empty_or_oversized_file")
        try:
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            if (
                reader.fieldnames is None
                or len(reader.fieldnames) != len(COLUMNS)
                or set(reader.fieldnames) != set(COLUMNS)
            ):
                raise InvalidCSV("invalid_header")
            rows = []
            for number, raw in enumerate(reader, 2):
                if len(rows) >= MAX_ROWS:
                    raise InvalidCSV("too_many_rows")
                if None in raw or any(value is None for value in raw.values()):
                    rows.append(
                        ParsedRow(
                            number,
                            {str(key): value for key, value in raw.items()},
                            None,
                            "column_count",
                        )
                    )
                    continue
                try:
                    values = {key: value.strip() for key, value in raw.items()}
                    observation_date = values.pop("observation_date")
                    if bool(observation_date) == bool(values["observed_at"]):
                        raise ValueError("Provide a date or timestamp, not both")
                    values["observation_precision"] = "day" if observation_date else "timestamp"
                    if observation_date:
                        from datetime import date

                        values["observed_at"] = datetime.combine(
                            date.fromisoformat(observation_date), time(), SL
                        ).isoformat()
                    values["price"] = (
                        None
                        if values["price"].casefold() in ("", "na", "n/a", "n.a.")
                        else values["price"]
                    )
                    for field in ["source_url", "source_published_at", "document_reference"]:
                        values[field] = values[field] or None
                    data = PriceInput.model_validate(values)
                    # Reject amounts that cannot be represented in the canonical precision.
                    normalized_price(data)
                    rows.append(ParsedRow(number, raw, data))
                except (ValidationError, ValueError, ArithmeticError):
                    rows.append(ParsedRow(number, raw, None, "invalid_value"))
            if not rows:
                raise InvalidCSV("no_data_rows")
            return rows
        except (UnicodeError, csv.Error) as exc:
            raise InvalidCSV("invalid_encoding_or_csv") from exc


def normalized_price(data: PriceInput) -> Decimal | None:
    if data.price is None:
        return None
    kg = (
        data.unit_quantity
        * {"kg": Decimal(1), "g": Decimal("0.001"), "tonne": Decimal(1000)}[data.unit]
    )
    value = (data.price / kg).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if value > Decimal("9999999999.999999") or data.price > 0 and value == 0:
        raise ValueError("Normalized value is outside supported precision")
    return value
