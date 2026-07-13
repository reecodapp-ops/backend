"""Middleware to log the raw request body of mutating requests before validation."""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("dukamate.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in ("POST", "PATCH", "PUT"):
            body = await request.body()
            logger.info(
                "%s %s body: %r",
                request.method,
                request.url.path,
                body,
            )

        response = await call_next(request)
        return response