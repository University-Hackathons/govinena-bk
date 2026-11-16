from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.v1.endpoints.logistics import Service as LogisticsDependency
from app.core.auth import UserDependency
from app.db.session import SessionDependency
from app.schemas.comparisons import ComparisonInput, ComparisonResponse
from app.services.market_comparison import MarketComparisonService

router = APIRouter(prefix="/comparisons", tags=["comparisons"])


def service(session: SessionDependency, logistics: LogisticsDependency, request: Request):
    return MarketComparisonService(
        session, logistics, request.app.state.settings.price_stale_after_hours
    )


Service = Annotated[MarketComparisonService, Depends(service)]


@router.post("", response_model=ComparisonResponse)
async def compare(
    data: ComparisonInput, user: UserDependency, service: Service, response: Response
):
    response.headers["Cache-Control"] = "private, no-store"
    return await service.compare(user.id, data)


@router.get("/{id}", response_model=ComparisonResponse)
def snapshot(id: UUID, user: UserDependency, service: Service, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return service.snapshot(user.id, id)
