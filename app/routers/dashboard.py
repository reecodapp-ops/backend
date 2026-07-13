"""Dashboard router (per blueprint F14). Requires X-Business-Id header.

    GET /dashboard/today             -> v_daily_summary + stock_purchases_today/profit_today
    GET /dashboard/debt-priority     -> v_debt_priority
    GET /dashboard/debt-aging        -> v_debt_aging
    GET /dashboard/stock-attention   -> v_stock_attention (?status=)
    GET /dashboard/margins           -> v_product_margin_30d
    GET /dashboard/expense-pressure  -> v_expense_pressure_30d
    GET /dashboard/activity          -> unified activity feed (today/yesterday/custom range, paginated)

No analytics are recomputed in Python for the view-backed endpoints; the views
own all aggregation logic. ``today`` and ``activity`` are the two documented
exceptions — see DashboardService/DashboardRepository.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.core.master_data_deps import DashboardServiceDep
from app.schemas.dashboard import (
    ActivityListOut,
    DailySummaryOut,
    DebtAgingListOut,
    DebtPriorityListOut,
    ExpensePressureListOut,
    ProductMarginListOut,
    StockAttentionListOut,
)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/today", response_model=DailySummaryOut)
async def get_today(service: DashboardServiceDep) -> DailySummaryOut:
    return await service.get_today()


@router.get("/debt-priority", response_model=DebtPriorityListOut)
async def get_debt_priority(service: DashboardServiceDep) -> DebtPriorityListOut:
    return await service.get_debt_priority()


@router.get("/debt-aging", response_model=DebtAgingListOut)
async def get_debt_aging(service: DashboardServiceDep) -> DebtAgingListOut:
    return await service.get_debt_aging()


@router.get("/stock-attention", response_model=StockAttentionListOut)
async def get_stock_attention(
    service: DashboardServiceDep,
    status: Annotated[
        Literal["OUT_OF_STOCK", "LOW_STOCK", "SLOW_MOVER", "OK"] | None, Query()
    ] = None,
) -> StockAttentionListOut:
    return await service.get_stock_attention(status)


@router.get("/margins", response_model=ProductMarginListOut)
async def get_margins(service: DashboardServiceDep) -> ProductMarginListOut:
    return await service.get_margins()


@router.get("/expense-pressure", response_model=ExpensePressureListOut)
async def get_expense_pressure(
    service: DashboardServiceDep,
) -> ExpensePressureListOut:
    return await service.get_expense_pressure()


@router.get(
    "/activity",
    response_model=ActivityListOut,
    summary="Unified activity feed across sales, payments, stock purchases, expenses, and capital",
)
async def get_activity(
    service: DashboardServiceDep,
    period: Annotated[
        Literal["today", "yesterday", "custom"],
        Query(description="today | yesterday | custom (use date_from/date_to)"),
    ] = "today",
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ActivityListOut:
    return await service.get_activity(
        period=period, date_from=date_from, date_to=date_to, limit=limit, offset=offset
    )
