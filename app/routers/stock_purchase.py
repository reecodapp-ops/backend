"""Stock Purchase router (per blueprint F10). Requires X-Business-Id header.

    POST /stock-purchases         record a purchase (items increment stock via trigger)
    GET  /stock-purchases         history (filterable, sortable, paginated)
    GET  /stock-purchases/{id}    detail
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.core.master_data_deps import StockPurchaseServiceDep
from app.schemas.stock_purchase import (
    StockPurchaseCreateRequest,
    StockPurchaseCreateResponse,
    StockPurchaseListOut,
    StockPurchaseOut,
)

router = APIRouter(prefix="/stock-purchases", tags=["Stock Purchases"])


@router.post(
    "",
    response_model=StockPurchaseCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_purchase(
    payload: StockPurchaseCreateRequest, service: StockPurchaseServiceDep
) -> StockPurchaseCreateResponse:
    return await service.create_purchase(payload)


@router.get("", response_model=StockPurchaseListOut)
async def list_purchases(
    service: StockPurchaseServiceDep,
    supplier_id: Annotated[uuid.UUID | None, Query()] = None,
    product_id: Annotated[
        uuid.UUID | None,
        Query(description="Purchases containing at least one line for this product"),
    ] = None,
    search: Annotated[
        str | None,
        Query(max_length=200, description="Search supplier_name_free / note"),
    ] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    order: Annotated[
        Literal["recent", "oldest", "cost_desc", "cost_asc"], Query()
    ] = "recent",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StockPurchaseListOut:
    return await service.list_purchases(
        supplier_id=supplier_id,
        product_id=product_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        order=order,
        limit=limit,
        offset=offset,
    )


@router.get("/{purchase_id}", response_model=StockPurchaseOut)
async def get_purchase(
    purchase_id: uuid.UUID, service: StockPurchaseServiceDep
) -> StockPurchaseOut:
    return await service.get_purchase(purchase_id)
