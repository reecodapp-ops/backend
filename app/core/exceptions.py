"""Domain exceptions mapped to consistent HTTP error envelopes.

All API errors return a uniform shape:
    {"error": {"code": "<machine_code>", "message": "<human readable>"}}

This includes FastAPI's own RequestValidationError (raised automatically when
a request body/query/path fails Pydantic validation) — handled here too so
clients never see FastAPI's default {"detail": [...]} shape, just the same
envelope as every other error, with the field-level details nested under
"errors" for debugging.
"""
from __future__ import annotations

import logging

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("dukamate.errors")


class AppError(Exception):
    """Base application error carrying an HTTP status and machine code."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.message = message
        if code:
            self.code = code
        super().__init__(message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class ValidationError(AppError):
    # 422 — literal to avoid version-specific deprecated constant access.
    status_code = 422
    code = "validation_error"


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "AppError on %s %s -> %s: %s",
        request.method,
        request.url.path,
        exc.code,
        exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle FastAPI/Pydantic request validation failures.

    Triggered automatically — before your endpoint or service code ever
    runs — when the request body, query params, or path params don't match
    the declared schema (e.g. BusinessCreateRequest). This is almost always
    the cause of a 422 on POST /businesses.

    exc.errors() is a list of dicts like:
        {
            "type": "greater_than_equal",
            "loc": ["body", "business_type_id"],
            "msg": "Input should be greater than or equal to 1",
            "input": 0,
            "ctx": {"error": ValueError(...)},  # <-- not JSON serializable!
        }

    Pydantic's "ctx" field can contain raw exception objects (e.g. when a
    custom @field_validator raises ValueError), which json.dumps cannot
    encode. We strip/stringify ctx before returning the payload to the
    client to avoid a 500 error from the JSON encoder itself.

    We log the full detail server-side (with the raw offending body) and
    return a sanitized version to the client nested under "errors", wrapped
    in the same {"error": {...}} envelope as every other AppError.
    """
    logger.warning(
        "Validation failed on %s %s: %s | raw body: %r",
        request.method,
        request.url.path,
        exc.errors(),
        exc.body,
    )

    safe_errors = []
    for err in exc.errors():
        safe_err = dict(err)
        ctx = safe_err.get("ctx")
        if isinstance(ctx, dict):
            safe_err["ctx"] = {k: str(v) for k, v in ctx.items()}
        safe_errors.append(safe_err)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed.",
                "errors": safe_errors,
            }
        },
    )


__all__ = [
    "AppError",
    "NotFoundError",
    "ConflictError",
    "UnauthorizedError",
    "ForbiddenError",
    "ValidationError",
    "app_error_handler",
    "request_validation_error_handler",
]