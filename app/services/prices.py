import hashlib
import json
import logging
from datetime import UTC, datetime, time, timedelta
from functools import wraps
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.models.catalog import Crop, Market
from app.models.prices import MarketPriceObservation as Observation
from app.models.prices import PriceImport, PriceImportRow
from app.providers.market_prices import InvalidCSV, MarketPriceProvider, ParsedRow, normalized_price
from app.repositories.prices import PriceRepository
from app.schemas.prices import (
    SL,
    ComparisonItem,
    ComparisonResponse,
    FreshnessResponse,
    HistoryResponse,
    ImportAudit,
    ImportReport,
    ImportRowResponse,
    ObservationResponse,
    PricePage,
    key_text,
    utc,
)

logger = logging.getLogger(__name__)


def storage_safe(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except SQLAlchemyError:
            self.session.rollback()
            raise HTTPException(503) from None

    return wrapped


class PriceService:
    def __init__(self, repository: PriceRepository, stale_after_hours=72):
        self.repo = repository
        self.session = repository.session
        self.stale_after_hours = stale_after_hours

    def validate_filters(self, filters):
        for name, model in [("crop_id", Crop), ("market_id", Market)]:
            if filters.get(name) is not None and self.session.get(model, filters[name]) is None:
                raise HTTPException(404)

    def response(self, row, now):
        record, crop, market = row
        age = max(0, (now - utc(record.observed_at)).total_seconds() / 3600)
        series_key = hashlib.sha256(
            json.dumps(
                [
                    record.crop_id,
                    record.market_id,
                    record.price_type,
                    record.quality_key,
                    record.source_key,
                ]
            ).encode()
        ).hexdigest()
        return ObservationResponse(
            series_key=series_key,
            id=record.id,
            crop_id=record.crop_id,
            crop_name=crop,
            market_id=record.market_id,
            market_name=market,
            observed_at=utc(record.observed_at),
            observation_precision=record.observation_precision,
            price_per_kg=record.price_per_kg,
            price_type=record.price_type,
            quality=record.quality,
            source_name=record.source_name,
            source_url=record.source_url,
            document_reference=record.document_reference,
            source_publication_date=record.source_publication_date,
            source_published_at=utc(record.source_published_at)
            if record.source_published_at
            else None,
            imported_at=utc(record.imported_at),
            import_id=record.import_id,
            freshness="missing"
            if record.price_per_kg is None
            else "stale"
            if age > self.stale_after_hours
            else "fresh",
            age_hours=round(age, 2),
        )

    @storage_safe
    def latest(self, filters, page=1, page_size=20):
        self.validate_filters(filters)
        now = datetime.now(UTC)
        rows, total = self.repo.latest(filters, page, page_size)
        return PricePage(
            items=[self.response(row, now) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            as_of=now,
            stale_after_hours=self.stale_after_hours,
        )

    @storage_safe
    def history(self, filters, start_date=None, end_date=None, limit=1000):
        self.validate_filters(filters)
        now = datetime.now(UTC)
        end_date = end_date or now.astimezone(SL).date()
        start_date = start_date or end_date - timedelta(days=30)
        if (
            start_date > end_date
            or (end_date - start_date).days > 366
            or end_date > now.astimezone(SL).date()
        ):
            raise HTTPException(422)
        start = datetime.combine(start_date, time(), SL).astimezone(UTC)
        end = datetime.combine(end_date + timedelta(days=1), time(), SL).astimezone(UTC)
        rows, total = self.repo.history(filters, start, end, limit)
        return HistoryResponse(
            items=[self.response(row, now) for row in rows],
            total=total,
            truncated=total > limit,
            start_date=start_date,
            end_date=end_date,
        )

    @storage_safe
    def comparison(self, filters, page=1, page_size=20):
        self.validate_filters(filters)
        markets = select(Market).where(Market.active.is_(True))
        if filters.get("market_id"):
            markets = markets.where(Market.id == filters["market_id"])
        total = self.session.scalar(select(func.count()).select_from(markets.subquery()))
        rows = self.session.scalars(
            markets.order_by(Market.name, Market.id).offset((page - 1) * page_size).limit(page_size)
        ).all()
        now = datetime.now(UTC)
        ids = [market.id for market in rows]
        observations = (
            self.session.execute(
                self.repo.records()
                .where(
                    Observation.id.in_(self.repo.latest_ids(filters)),
                    Observation.market_id.in_(ids),
                )
                .order_by(Observation.source_key, Observation.quality_key)
            ).all()
            if ids
            else []
        )
        return ComparisonResponse(
            items=[
                ComparisonItem(
                    market_id=market.id,
                    market_name=market.name,
                    district=market.district,
                    observations=[
                        self.response(row, now)
                        for row in observations
                        if row[0].market_id == market.id
                    ],
                )
                for market in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
            as_of=now,
        )

    @storage_safe
    def freshness(self, filters):
        self.validate_filters(filters)
        now = datetime.now(UTC)
        latest = (
            select(Observation).where(Observation.id.in_(self.repo.latest_ids(filters))).subquery()
        )
        cutoff = now - timedelta(hours=self.stale_after_hours)
        row = self.session.execute(
            select(
                func.sum(
                    case(
                        (latest.c.price_per_kg.is_not(None) & (latest.c.observed_at >= cutoff), 1),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (latest.c.price_per_kg.is_not(None) & (latest.c.observed_at < cutoff), 1),
                        else_=0,
                    )
                ),
                func.sum(case((latest.c.price_per_kg.is_(None), 1), else_=0)),
                func.max(case((latest.c.price_per_kg.is_not(None), latest.c.observed_at))),
                func.max(latest.c.imported_at),
            )
        ).one()
        crops = select(func.count()).select_from(Crop).where(Crop.active.is_(True))
        markets = select(func.count()).select_from(Market).where(Market.active.is_(True))
        if filters.get("crop_id"):
            crops = crops.where(Crop.id == filters["crop_id"])
        if filters.get("market_id"):
            markets = markets.where(Market.id == filters["market_id"])
        expected = self.session.scalar(crops) * self.session.scalar(markets)
        pairs = (
            select(latest.c.crop_id, latest.c.market_id)
            .where(latest.c.price_per_kg.is_not(None))
            .distinct()
            .subquery()
        )
        covered = self.session.scalar(select(func.count()).select_from(pairs))
        return FreshnessResponse(
            as_of=now,
            stale_after_hours=self.stale_after_hours,
            fresh_series=row[0] or 0,
            stale_series=row[1] or 0,
            missing_series=row[2] or 0,
            expected_market_crop_pairs=expected,
            pairs_without_reported_price=max(0, expected - covered),
            last_observed_at=utc(row[3]) if row[3] else None,
            last_imported_at=utc(row[4]) if row[4] else None,
        )

    @staticmethod
    def report(record, replayed=False):
        fields = {
            name: getattr(record, name)
            for name in ImportReport.model_fields
            if name not in ("replayed", "imported_at")
        }
        return ImportReport(**fields, imported_at=utc(record.imported_at), replayed=replayed)

    @storage_safe
    def import_csv(
        self,
        content: bytes,
        user_id: UUID,
        filename: str,
        verification_note: str,
        provider: MarketPriceProvider,
    ):
        now, id = datetime.now(UTC), uuid4()
        digest = hashlib.sha256(content).hexdigest()
        inserted = self.repo.insert_once(
            PriceImport,
            dict(
                id=id,
                sha256=digest,
                imported_by=user_id,
                imported_at=now,
                filename=filename,
                provider="verified_csv_v1",
                verification_note=verification_note,
                original_file=content,
                accepted=0,
                duplicates=0,
                rejected=0,
            ),
            "sha256",
        )
        if not inserted:
            record = self.session.scalar(select(PriceImport).where(PriceImport.sha256 == digest))
            self.session.rollback()
            logger.info(
                "Price CSV replayed",
                extra={"import_id": str(record.id), "requested_by": str(user_id)},
            )
            return self.report(record, True)
        record = self.session.get(PriceImport, id)
        try:
            rows = provider.parse(content)
        except InvalidCSV as exc:
            rows = [ParsedRow(0, {}, None, str(exc))]
        crops = {crop.id: crop for crop in self.session.scalars(select(Crop)).all()}
        markets = {market.id: market for market in self.session.scalars(select(Market)).all()}
        for row in rows:
            outcome, error, observation_id = "rejected", row.error, None
            if row.data is not None:
                data = row.data
                crop, market = crops.get(data.crop_id), markets.get(data.market_id)
                if crop is None or market is None:
                    error = "unknown_crop_or_market"
                elif not crop.active or not market.active:
                    error = "inactive_crop_or_market"
                else:
                    natural_key = hashlib.sha256(
                        json.dumps(
                            [
                                data.crop_id,
                                data.market_id,
                                data.observed_at.isoformat(),
                                data.price_type,
                                key_text(data.quality),
                                key_text(data.source_name),
                            ],
                            ensure_ascii=False,
                        ).encode()
                    ).hexdigest()
                    price = normalized_price(data)
                    new_id = uuid4()
                    values = dict(
                        id=new_id,
                        natural_key=natural_key,
                        crop_id=data.crop_id,
                        market_id=data.market_id,
                        observed_at=data.observed_at,
                        observation_precision=data.observation_precision,
                        price_per_kg=price,
                        price_type=data.price_type,
                        quality=data.quality,
                        quality_key=key_text(data.quality),
                        source_name=data.source_name,
                        source_key=key_text(data.source_name),
                        source_url=str(data.source_url) if data.source_url else None,
                        document_reference=data.document_reference,
                        source_publication_date=data.source_publication_date,
                        source_published_at=data.source_published_at,
                        imported_at=now,
                        import_id=id,
                        source_row_number=row.number,
                    )
                    if self.repo.insert_once(Observation, values, "natural_key"):
                        outcome, error, observation_id = "accepted", None, new_id
                    else:
                        existing = self.session.scalar(
                            select(Observation).where(Observation.natural_key == natural_key)
                        )
                        if existing.price_per_kg == price:
                            outcome, error, observation_id = "duplicate", None, existing.id
                        else:
                            error = "conflicting_observation"
            if outcome == "accepted":
                record.accepted += 1
            elif outcome == "duplicate":
                record.duplicates += 1
            else:
                record.rejected += 1
                row.error = error
            self.session.add(
                PriceImportRow(
                    import_id=id,
                    row_number=row.number,
                    raw_data=row.raw,
                    outcome=outcome,
                    error_code=error,
                    observation_id=observation_id,
                )
            )
        self.session.commit()
        for row in rows:
            if row.error:
                logger.warning(
                    "Price CSV row rejected",
                    extra={"import_id": str(id), "row_number": row.number, "error_code": row.error},
                )
        logger.info(
            "Price CSV import completed",
            extra={
                "import_id": str(id),
                "accepted": record.accepted,
                "duplicates": record.duplicates,
                "rejected": record.rejected,
            },
        )
        return self.report(record)

    @storage_safe
    def audit(self, id, page=1, page_size=50):
        record = self.repo.import_report(id)
        if record is None:
            raise HTTPException(404)
        rows, total = self.repo.import_rows(id, page, page_size)
        return ImportAudit(
            report=self.report(record),
            items=[
                ImportRowResponse(
                    row_number=row.row_number,
                    outcome=row.outcome,
                    error_code=row.error_code,
                    observation_id=row.observation_id,
                    raw_data=row.raw_data,
                )
                for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    @storage_safe
    def original_file(self, id):
        record = self.repo.import_report(id)
        if record is None:
            raise HTTPException(404)
        return record.original_file
