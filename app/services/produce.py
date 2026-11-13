from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.models.catalog import Crop
from app.models.farmer import FarmerProfile
from app.models.produce import ProduceBatch
from app.repositories.produce import ProduceRepository
from app.schemas.catalog import PageResponse
from app.schemas.produce import (
    CropSummary,
    ProduceCreate,
    ProducePatch,
    ProduceResponse,
    SourceLocation,
    utc,
)


class ProduceService:
    def __init__(self, repository: ProduceRepository):
        self.repository = repository
        self.session = repository.session

    def default_location(self, farmer_id: UUID) -> SourceLocation:
        profile = self.session.get(FarmerProfile, farmer_id)
        if profile is None:
            raise HTTPException(409)
        return SourceLocation(
            district=profile.district,
            town=profile.town,
            latitude=profile.latitude,
            longitude=profile.longitude,
        )

    def require_crop(self, crop_id: str):
        crop = self.session.get(Crop, crop_id)
        if crop is None or not crop.active:
            raise HTTPException(422)

    @staticmethod
    def apply(batch: ProduceBatch, values: ProduceCreate, location: SourceLocation):
        for name in (
            "crop_id",
            "quantity_kg",
            "quality",
            "notes",
            "harvested_at",
            "sell_by",
            "status",
        ):
            setattr(batch, name, getattr(values, name))
        for name in ("district", "town", "latitude", "longitude"):
            setattr(batch, f"source_{name}", getattr(location, name))
        batch.updated_at = datetime.now(UTC)
        if values.status == "archived":
            batch.archived_at = batch.updated_at

    def create(self, farmer_id: UUID, data: ProduceCreate) -> ProduceResponse:
        try:
            # A farmer profile is required even when an explicit location is supplied.
            default = self.default_location(farmer_id)
            self.require_crop(data.crop_id)
            batch = ProduceBatch(farmer_id=farmer_id)
            self.apply(batch, data, data.source_location or default)
            self.session.add(batch)
            self.session.commit()
            self.session.refresh(batch)
            return self.response(batch)
        except SQLAlchemyError:
            self.session.rollback()
            raise HTTPException(503) from None

    def list(self, farmer_id: UUID, **filters) -> PageResponse[ProduceResponse]:
        try:
            self.repository.expire_due(farmer_id, datetime.now(UTC))
            self.session.commit()
            items, total = self.repository.list(farmer_id, **filters)
            return PageResponse[ProduceResponse](
                items=[self.response(row) for row in items],
                total=total,
                page=filters["page"],
                page_size=filters["page_size"],
            )
        except SQLAlchemyError:
            self.session.rollback()
            raise HTTPException(503) from None

    def get(self, farmer_id: UUID, batch_id: UUID) -> ProduceResponse:
        try:
            self.repository.expire_due(farmer_id, datetime.now(UTC))
            self.session.commit()
            batch = self.repository.get(farmer_id, batch_id)
            if batch is None:
                raise HTTPException(404)
            return self.response(batch)
        except SQLAlchemyError:
            self.session.rollback()
            raise HTTPException(503) from None

    def patch(self, farmer_id: UUID, batch_id: UUID, patch: ProducePatch) -> ProduceResponse:
        try:
            self.repository.expire_due(farmer_id, datetime.now(UTC))
            batch = self.repository.get(farmer_id, batch_id, lock=True)
            if batch is None:
                raise HTTPException(404)
            if batch.status == "archived":
                raise HTTPException(409)
            values = {
                name: getattr(batch, name)
                for name in ("crop_id", "quantity_kg", "quality", "notes", "status")
            } | {"harvested_at": utc(batch.harvested_at), "sell_by": utc(batch.sell_by)}
            location = self.response(batch).source_location
            changes = patch.model_dump(exclude_unset=True)
            if "source_location" in changes:
                location = patch.source_location or self.default_location(farmer_id)
            changes.pop("source_location", None)
            try:
                merged = ProduceCreate.model_validate(values | changes)
            except ValidationError:
                raise HTTPException(422) from None
            if merged.crop_id != batch.crop_id:
                self.require_crop(merged.crop_id)
            self.apply(batch, merged, location)
            self.session.commit()
            self.session.refresh(batch)
            return self.response(batch)
        except SQLAlchemyError:
            self.session.rollback()
            raise HTTPException(503) from None

    def archive(self, farmer_id: UUID, batch_id: UUID):
        try:
            batch = self.repository.get(farmer_id, batch_id, lock=True)
            if batch is None:
                raise HTTPException(404)
            if batch.status != "archived":
                batch.status = "archived"
                batch.archived_at = batch.updated_at = datetime.now(UTC)
                self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise HTTPException(503) from None

    @staticmethod
    def response(batch: ProduceBatch) -> ProduceResponse:
        return ProduceResponse(
            id=batch.id,
            farmer_id=batch.farmer_id,
            crop_id=batch.crop_id,
            crop=CropSummary.model_validate(batch.crop),
            quantity_kg=batch.quantity_kg,
            quality=batch.quality,
            notes=batch.notes,
            status=batch.status,
            harvested_at=utc(batch.harvested_at),
            sell_by=utc(batch.sell_by),
            source_location=SourceLocation(
                district=batch.source_district,
                town=batch.source_town,
                latitude=batch.source_latitude,
                longitude=batch.source_longitude,
            ),
            archived_at=utc(batch.archived_at) if batch.archived_at else None,
            created_at=utc(batch.created_at),
            updated_at=utc(batch.updated_at),
        )
