from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.core.auth import UserDependency
from app.db.session import SessionDependency
from app.repositories.produce import ProduceRepository
from app.schemas.catalog import PageResponse
from app.schemas.produce import BatchStatus, ProduceCreate, ProducePatch, ProduceResponse
from app.services.produce import ProduceService

router = APIRouter(prefix="/produce", tags=["produce"])


def produce_service(session: SessionDependency) -> ProduceService:
    return ProduceService(ProduceRepository(session))


Service = Annotated[ProduceService, Depends(produce_service)]


@router.post("", response_model=ProduceResponse, status_code=201)
def create_produce(data: ProduceCreate, user: UserDependency, service: Service, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return service.create(user.id, data)


@router.get("", response_model=PageResponse[ProduceResponse])
def list_produce(
    user: UserDependency,
    service: Service,
    response: Response,
    q: Annotated[str, Query(max_length=100)] = "",
    status: BatchStatus | None = None,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 12,
):
    response.headers["Cache-Control"] = "no-store"
    return service.list(user.id, q=q, status=status, page=page, page_size=page_size)


@router.get("/{id}", response_model=ProduceResponse)
def get_produce(id: UUID, user: UserDependency, service: Service, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return service.get(user.id, id)


@router.patch("/{id}", response_model=ProduceResponse)
def patch_produce(
    id: UUID, data: ProducePatch, user: UserDependency, service: Service, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    return service.patch(user.id, id, data)


@router.delete("/{id}", status_code=204)
def delete_produce(id: UUID, user: UserDependency, service: Service):
    """Archive without physically deleting the batch or breaking future history links."""
    service.archive(user.id, id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
