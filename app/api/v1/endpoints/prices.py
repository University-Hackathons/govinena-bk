from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.core.auth import AdminDependency, IdentityDependency
from app.db.session import SessionDependency
from app.providers.market_prices import MAX_BYTES, VerifiedCSVProvider
from app.repositories.prices import PriceRepository
from app.schemas.catalog import CatalogID
from app.schemas.prices import (
    ComparisonResponse,
    FreshnessResponse,
    HistoryResponse,
    ImportAudit,
    ImportReport,
    PricePage,
    PriceType,
    key_text,
)
from app.services.prices import PriceService

router = APIRouter(prefix="/prices", tags=["market prices"])


def service(session: SessionDependency, request: Request):
    return PriceService(
        PriceRepository(session), request.app.state.settings.price_stale_after_hours
    )


Service = Annotated[PriceService, Depends(service)]
Page = Annotated[int, Query(ge=1, le=1000000)]
Size = Annotated[int, Query(ge=1, le=100)]


def filters(
    crop_id: CatalogID | None = None,
    market_id: CatalogID | None = None,
    price_type: PriceType = "wholesale",
    quality: Annotated[str | None, Query(max_length=80)] = None,
    source_name: Annotated[str | None, Query(max_length=160)] = None,
):
    return dict(
        crop_id=crop_id,
        market_id=market_id,
        price_type=price_type,
        quality_key=key_text(quality) if quality is not None else None,
        source_key=key_text(source_name) if source_name is not None else None,
    )


Filters = Annotated[dict, Depends(filters)]


@router.get("/access")
def access(identity: IdentityDependency, request: Request):
    return {"can_import": identity.user_id in request.app.state.settings.admin_user_ids}


@router.get("/latest", response_model=PricePage)
def latest(
    identity: IdentityDependency,
    service: Service,
    filters: Filters,
    response: Response,
    page: Page = 1,
    page_size: Size = 20,
):
    response.headers["Cache-Control"] = "no-store"
    return service.latest(filters, page, page_size)


@router.get("/history", response_model=HistoryResponse)
def history(
    identity: IdentityDependency,
    service: Service,
    response: Response,
    crop_id: CatalogID,
    market_id: CatalogID,
    price_type: PriceType = "wholesale",
    start_date: date | None = None,
    end_date: date | None = None,
    quality: Annotated[str | None, Query(max_length=80)] = None,
    source_name: Annotated[str | None, Query(max_length=160)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
):
    response.headers["Cache-Control"] = "no-store"
    return service.history(
        dict(
            crop_id=crop_id,
            market_id=market_id,
            price_type=price_type,
            quality_key=key_text(quality) if quality is not None else None,
            source_key=key_text(source_name) if source_name is not None else None,
        ),
        start_date,
        end_date,
        limit,
    )


@router.get("/comparison", response_model=ComparisonResponse)
def comparison(
    identity: IdentityDependency,
    service: Service,
    filters: Filters,
    crop_id: CatalogID,
    response: Response,
    page: Page = 1,
    page_size: Size = 20,
):
    response.headers["Cache-Control"] = "no-store"
    return service.comparison(filters | {"crop_id": crop_id}, page, page_size)


@router.get("/freshness", response_model=FreshnessResponse)
def freshness(identity: IdentityDependency, service: Service, filters: Filters, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return service.freshness(filters)


@router.post("/imports", response_model=ImportReport)
async def import_prices(
    admin: AdminDependency,
    service: Service,
    request: Request,
    verification_note: Annotated[str, Query(min_length=10, max_length=1000)],
    verified: bool,
    filename: Annotated[str, Query(min_length=1, max_length=120)] = "prices.csv",
):
    if not verified or len(verification_note.strip()) < 10:
        raise HTTPException(422)
    if request.headers.get("content-type", "").split(";")[0] not in ("text/csv", "application/csv"):
        raise HTTPException(415)
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_BYTES:
            raise HTTPException(413)
    safe_name = filename.replace("\\", "/").split("/")[-1]
    if not safe_name or any(ord(char) < 32 for char in safe_name):
        raise HTTPException(422)
    result = await run_in_threadpool(
        service.import_csv,
        bytes(content),
        admin.id,
        safe_name,
        verification_note.strip(),
        VerifiedCSVProvider(),
    )
    return Response(
        content=result.model_dump_json(),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/imports/{id}", response_model=ImportAudit)
def import_audit(
    id: UUID,
    admin: AdminDependency,
    service: Service,
    response: Response,
    page: Page = 1,
    page_size: Size = 50,
):
    response.headers["Cache-Control"] = "no-store"
    return service.audit(id, page, page_size)


@router.get("/imports/{id}/file")
def original_import_file(id: UUID, admin: AdminDependency, service: Service):
    return Response(
        content=service.original_file(id),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="price-import-{id}.csv"',
            "X-Content-Type-Options": "nosniff",
        },
    )
