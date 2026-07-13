"""DukaMate API application entrypoint.

Registers routers, the uniform error handler, and a health check. Business and
event modules are mounted here as they are implemented (per blueprint build order).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.exceptions import (
    AppError,
    app_error_handler,
    request_validation_error_handler,
)
from app.core.request_logging_middleware import RequestLoggingMiddleware
from app.routers import auth, business, capital, correction, customer, dashboard, expense, issue, lookups, payment, product, sale, stock_purchase, supplier, sync


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        version="1.0.0",
    )

    # NOTE: Middleware added LAST becomes the OUTERMOST layer in Starlette/FastAPI.
    # RequestLoggingMiddleware must be added BEFORE CORSMiddleware so that
    # CORSMiddleware wraps everything (including any errors raised inside
    # RequestLoggingMiddleware) and can still attach CORS headers to the response.
    # Getting this order backwards causes real 500 errors to show up in the
    # browser as a fake "CORS policy" block, which is misleading to debug.

    # Logs the raw body of POST/PATCH/PUT requests before validation runs.
    app.add_middleware(RequestLoggingMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            #"https://your-frontend-domain.vercel.app",   # <-- add your real deployed frontend URL here later
            "https://frontend-production-fe9f.up.railway.app",  # <-- or here, if you deploy the frontend on Railway instead
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)

    app.include_router(auth.router, prefix=settings.api_v1_prefix)
    app.include_router(lookups.router, prefix=settings.api_v1_prefix)
    app.include_router(lookups.countries_router, prefix=settings.api_v1_prefix)
    app.include_router(lookups.plans_router, prefix=settings.api_v1_prefix)
    app.include_router(lookups.phone_numbers_router, prefix=settings.api_v1_prefix)
    app.include_router(business.router, prefix=settings.api_v1_prefix)
    app.include_router(customer.router, prefix=settings.api_v1_prefix)
    app.include_router(supplier.router, prefix=settings.api_v1_prefix)
    app.include_router(product.category_router, prefix=settings.api_v1_prefix)
    app.include_router(product.product_router, prefix=settings.api_v1_prefix)
    app.include_router(sale.router, prefix=settings.api_v1_prefix)
    app.include_router(payment.router, prefix=settings.api_v1_prefix)
    app.include_router(expense.router, prefix=settings.api_v1_prefix)
    app.include_router(expense.category_router, prefix=settings.api_v1_prefix)
    app.include_router(stock_purchase.router, prefix=settings.api_v1_prefix)
    app.include_router(capital.router, prefix=settings.api_v1_prefix)
    app.include_router(capital.sources_router, prefix=settings.api_v1_prefix)
    app.include_router(dashboard.router, prefix=settings.api_v1_prefix)
    app.include_router(correction.router, prefix=settings.api_v1_prefix)
    app.include_router(issue.router, prefix=settings.api_v1_prefix)
    app.include_router(sync.router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["Health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name}

    return app


app = create_app()