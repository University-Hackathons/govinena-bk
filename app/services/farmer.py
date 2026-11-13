from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.models.farmer import FarmerProfile
from app.repositories.farmer import FarmerProfileRepository
from app.schemas.farmer import FarmerProfileInput, FarmerProfileResponse, ProfileCompletion


class FarmerProfileService:
    def __init__(self, repository: FarmerProfileRepository):
        self.repository = repository

    def get(self, user_id: UUID) -> FarmerProfileResponse | None:
        profile = self.repository.for_user(user_id)
        return self.response(profile) if profile else None

    def put(self, user_id: UUID, data: FarmerProfileInput) -> FarmerProfileResponse:
        values = data.model_dump()
        try:
            profile = self.repository.for_user(user_id)
            if profile is None:
                profile = FarmerProfile(user_id=user_id, **values)
            else:
                for field, value in values.items():
                    setattr(profile, field, value)
            profile.updated_at = datetime.now(UTC)
            self.repository.save(profile)
            self.repository.session.commit()
        except IntegrityError:
            # A concurrent first save may have created this same user's row.
            self.repository.session.rollback()
            profile = self.repository.for_user(user_id)
            if profile is None:
                raise
            for field, value in values.items():
                setattr(profile, field, value)
            profile.updated_at = datetime.now(UTC)
            self.repository.save(profile)
            self.repository.session.commit()
        self.repository.session.refresh(profile)
        return self.response(profile)

    @staticmethod
    def response(profile: FarmerProfile) -> FarmerProfileResponse:
        optional = sum(
            [
                bool(profile.contact_number),
                profile.latitude is not None,
                profile.vehicle_capacity_kg is not None,
                profile.estimated_transport_rate is not None,
                bool(profile.preferred_market_ids),
            ]
        )
        return FarmerProfileResponse(
            **{field: getattr(profile, field) for field in FarmerProfileInput.model_fields},
            user_id=profile.user_id,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            completion=ProfileCompletion(
                required_complete=True,
                optional_completed=optional,
                optional_total=5,
                percentage=round((5 + optional) / 10 * 100),
            ),
        )
