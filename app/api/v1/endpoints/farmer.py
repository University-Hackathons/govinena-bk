from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.auth import UserDependency
from app.db.session import SessionDependency
from app.repositories.farmer import FarmerProfileRepository
from app.schemas.farmer import FarmerProfileInput, FarmerProfileResponse
from app.services.farmer import FarmerProfileService

router = APIRouter(prefix="/farmer", tags=["farmer"])


def farmer_service(session: SessionDependency) -> FarmerProfileService:
    return FarmerProfileService(FarmerProfileRepository(session))


Service = Annotated[FarmerProfileService, Depends(farmer_service)]


@router.get("/profile", response_model=FarmerProfileResponse)
def get_profile(user: UserDependency, service: Service, response: Response):
    profile = service.get(user.id)
    if profile is None:
        raise HTTPException(404, headers={"Cache-Control": "no-store"})
    response.headers["Cache-Control"] = "no-store"
    return profile


@router.put("/profile", response_model=FarmerProfileResponse)
def put_profile(
    payload: FarmerProfileInput,
    user: UserDependency,
    service: Service,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    return service.put(user.id, payload)
