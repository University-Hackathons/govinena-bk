"""JSON logs with an explicit allowlist of context fields."""

import json
import logging
from datetime import UTC, datetime
from logging.config import dictConfig
from time import perf_counter
from uuid import uuid4

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import unexpected_error_handler


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "route", "status_code", "duration_ms", "error_type"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        # Do not serialize exception messages, tracebacks, arbitrary extras, or settings.
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                "uvicorn": {"handlers": [], "propagate": True, "level": level},
                "uvicorn.error": {"handlers": [], "propagate": True, "level": level},
                "uvicorn.access": {"handlers": [], "propagate": False},
                "httpx": {"handlers": [], "propagate": True, "level": "WARNING"},
                "httpx2": {"handlers": [], "propagate": True, "level": "WARNING"},
                "httpcore": {"handlers": [], "propagate": True, "level": "WARNING"},
            },
        }
    )


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = logging.getLogger("app.requests")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        request_id = uuid4().hex
        status_code = 500
        response_started = False

        async def send_response(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_response)
        except Exception as exc:
            # Handle before CORS sends the response; suppress unsafe server traceback logs.
            response = await unexpected_error_handler(Request(scope), exc)
            if not response_started:
                await response(scope, receive, send_response)
        finally:
            route = scope.get("route")
            self.logger.info(
                "Request completed",
                extra={
                    "request_id": request_id,
                    "method": scope["method"],
                    "route": getattr(route, "path", "[unmatched]"),
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                },
            )
