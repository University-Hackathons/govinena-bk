from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.auth import AdminDependency, UserDependency
from app.core.errors import error_response
from app.db.session import SessionDependency
from app.models.catalog import Crop
from app.models.crop_risk import CropRiskProfile
from app.repositories.produce import ProduceRepository
from app.schemas.crop_risk import EvaluateInput, ProfileResponse, RiskResponse, RiskRules
from app.schemas.produce import utc
from app.services.crop_risk import RiskTimeError, evaluate_risk

router = APIRouter(prefix="/crop-risk", tags=["crop-risk"])


def latest(session, crop_id):
    return session.scalar(
        select(CropRiskProfile)
        .where(CropRiskProfile.crop_id == crop_id)
        .order_by(CropRiskProfile.version.desc())
        .limit(1)
    )


def profile_response(row):
    return ProfileResponse(
        **row.rules,
        crop_id=row.crop_id,
        version=row.version,
        created_by=row.created_by,
        created_at=utc(row.created_at),
    )


@router.post("/evaluate", response_model=RiskResponse)
def evaluate(
    data: EvaluateInput, user: UserDependency, session: SessionDependency, response: Response
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        batch = ProduceRepository(session).get(user.id, data.produce_batch_id)
        if batch is None:
            raise HTTPException(404)
        row = latest(session, batch.crop_id)
        return evaluate_risk(
            batch_id=batch.id,
            crop_id=batch.crop_id,
            quantity=batch.quantity_kg,
            harvested_at=batch.harvested_at,
            deadline=batch.sell_by,
            assessment_at=data.assessment_at or datetime.now(UTC),
            arrival_at=data.expected_arrival_at,
            storage=data.storage_condition,
            rules=RiskRules.model_validate(row.rules) if row else None,
            profile_version=row.version if row else None,
            closed=batch.status in {"sold", "archived"},
        )
    except RiskTimeError:
        return error_response(
            422,
            "risk_time_validation",
            "Assessment must follow harvest; arrival must not precede assessment.",
            headers={"Cache-Control": "private, no-store"},
        )
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503) from None


@router.get("/profiles/{crop_id}", response_model=ProfileResponse)
def current_profile(
    crop_id: str, user: UserDependency, session: SessionDependency, response: Response
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        row = latest(session, crop_id)
        if row is None:
            raise HTTPException(404)
        return profile_response(row)
    except SQLAlchemyError:
        raise HTTPException(503) from None


@router.get("/profiles/{crop_id}/versions", response_model=list[ProfileResponse])
def versions(crop_id: str, admin: AdminDependency, session: SessionDependency, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        if session.get(Crop, crop_id) is None:
            raise HTTPException(404)
        rows = session.scalars(
            select(CropRiskProfile)
            .where(CropRiskProfile.crop_id == crop_id)
            .order_by(CropRiskProfile.version.desc())
        ).all()
        return [profile_response(row) for row in rows]
    except SQLAlchemyError:
        raise HTTPException(503) from None


@router.post("/profiles/{crop_id}", response_model=ProfileResponse, status_code=201)
def revise_profile(
    crop_id: str,
    data: RiskRules,
    admin: AdminDependency,
    session: SessionDependency,
    response: Response,
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        # Serialize versions per crop in PostgreSQL; uniqueness also protects other engines.
        crop = session.scalar(select(Crop).where(Crop.id == crop_id).with_for_update())
        if crop is None:
            raise HTTPException(404)
        previous = latest(session, crop_id)
        row = CropRiskProfile(
            crop_id=crop_id,
            version=previous.version + 1 if previous else 1,
            enabled=data.enabled,
            rules=data.model_dump(mode="json"),
            created_by=admin.id,
            created_at=datetime.now(UTC),
        )
        session.add(row)
        session.commit()
        return profile_response(row)
    except IntegrityError:
        session.rollback()
        raise HTTPException(409) from None
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503) from None
