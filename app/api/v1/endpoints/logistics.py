from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request, Response

from app.core.auth import UserDependency
from app.core.errors import error_response
from app.db.session import SessionDependency
from app.providers.routes import MapboxRouteProvider, RouteProvider
from app.repositories.logistics import LogisticsRepository
from app.schemas.errors import ValidationIssue
from app.schemas.logistics import EstimateHistory, LogisticsInput, LogisticsResponse
from app.services.external import get_http_client
from app.services.logistics import LogisticsService
from app.services.transport_cost import LogisticsValidationError

router = APIRouter(prefix="/logistics", tags=["logistics"])


def route_provider(
    request: Request, client: Annotated[httpx.AsyncClient, Depends(get_http_client)]
) -> RouteProvider | None:
    settings = request.app.state.settings
    if settings.map_provider != "mapbox" or not settings.mapbox_access_token:
        return None
    return MapboxRouteProvider(
        client, settings.mapbox_access_token.get_secret_value(), settings.routing_allow_mapbox_cache
    )


def service(
    session: SessionDependency,
    request: Request,
    provider: Annotated[RouteProvider | None, Depends(route_provider)],
):
    return LogisticsService(
        LogisticsRepository(session),
        provider,
        request.app.state.route_cache,
        request.app.state.settings.route_cache_ttl_seconds,
    )


Service = Annotated[LogisticsService, Depends(service)]


@router.post("/estimate", response_model=LogisticsResponse)
async def estimate(
    data: LogisticsInput, user: UserDependency, service: Service, response: Response
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return await service.estimate(user.id, data)
    except LogisticsValidationError as exc:
        return error_response(
            422,
            "logistics_validation",
            exc.message,
            [ValidationIssue(location=["body", exc.field], type="logistics_validation")],
            headers={"Cache-Control": "private, no-store"},
        )


@router.get("/estimates", response_model=EstimateHistory)
def history(batch_id: UUID, user: UserDependency, service: Service, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return service.history(user.id, batch_id)
