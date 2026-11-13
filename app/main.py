from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints.health import router as health_router
from app.api.v1.router import router as v1_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.db.session import build_engine, build_session_factory
from app.schemas.errors import ErrorResponse


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level)
        try:
            yield
        finally:
            app.state.engine.dispose()

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="GoviNena farmer market decision support backend.",
        lifespan=lifespan,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    application.state.settings = settings
    application.state.engine = build_engine(settings)
    application.state.session_factory = build_session_factory(application.state.engine)
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(v1_router, prefix="/api/v1")
    application.add_middleware(RequestLoggingMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin).rstrip("/") for origin in settings.cors_origins],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Request-ID"],
    )
    return application


app = create_app()
