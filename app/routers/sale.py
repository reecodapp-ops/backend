"""Sales router (per blueprint F6/F7 + Receipts feature set). Requires
X-Business-Id header.

    POST /sales                          create a cash or debt sale
    GET  /sales                          list/filter sales
    GET  /sales/today                    list sales made today
    GET  /sales/{sale_id}                fetch a single sale with its items
    GET  /sales/{sale_id}/receipt        receipt preview (read-only render)
    POST /sales/{sale_id}/receipt/regenerate  re-issue the receipt number
    POST /sales/{sale_id}/share-receipt  record a share + optional WhatsApp link

Receipt correction (editing a sale's total after the fact) is handled by the
generic Corrections module — see ``POST /corrections`` with
``correction_type_code: "SALE_EDIT"`` — rather than duplicated here.

A single endpoint handles both cash and debt sales; ``payment_type`` in the body
discriminates. Aggregate updates (cash, debt, stock, debt ledger, credit score)
are performed by database triggers — this router only records the raw sale and
its items, and reads sales/receipts back out.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, status

from app.core.master_data_deps import SaleServiceDep
from app.schemas.sale import (
    ReceiptOut,
    SaleCreateRequest,
    SaleCreateResponse,
    SaleListResponse,
    SaleOut,
    SaleReceiptShareRequest,
)

router = APIRouter(prefix="/sales", tags=["Sales"])


@router.post(
    "",
    response_model=SaleCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a sale (cash or debt)",
)
async def create_sale(
    payload: SaleCreateRequest,
    service: SaleServiceDep,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=200,
            description=(
                "Optional client-generated key (a UUID is recommended). "
                "Replaying a POST with the same key within 24h returns the "
                "original response instead of creating a second sale."
            ),
        ),
    ] = None,
) -> SaleCreateResponse:
    return await service.create_sale(payload, idempotency_key=idempotency_key)


@router.get(
    "",
    response_model=SaleListResponse,
    summary="List sales for the business (filterable, sortable, paginated)",
)
async def list_sales(
    service: SaleServiceDep,
    start_date: Annotated[
        date | None, Query(description="Inclusive lower bound on occurred_at date.")
    ] = None,
    end_date: Annotated[
        date | None, Query(description="Inclusive upper bound on occurred_at date.")
    ] = None,
    customer_id: Annotated[uuid.UUID | None, Query()] = None,
    payment_type: Annotated[
        Literal["CASH", "ON_DEBT"] | None,
        Query(description="Filter to cash-only or debt-only sales"),
    ] = None,
    receipt_number: Annotated[
        str | None, Query(max_length=30, description="Partial receipt number match")
    ] = None,
    search: Annotated[
        str | None,
        Query(
            max_length=200,
            description="Single search box: matches receipt number OR customer name",
        ),
    ] = None,
    order: Annotated[
        Literal["recent", "oldest", "amount_desc", "amount_asc"], Query()
    ] = "recent",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SaleListResponse:
    return await service.list_sales(
        start_date=start_date,
        end_date=end_date,
        customer_id=customer_id,
        payment_type=payment_type,
        receipt_number=receipt_number,
        search=search,
        order=order,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/today",
    response_model=SaleListResponse,
    summary="List sales made today",
)
async def list_today_sales(service: SaleServiceDep) -> SaleListResponse:
    return await service.list_today_sales()


@router.get(
    "/{sale_id}",
    response_model=SaleOut,
    summary="Get a single sale by id",
)
async def get_sale(sale_id: uuid.UUID, service: SaleServiceDep) -> SaleOut:
    return await service.get_sale(sale_id)


@router.get(
    "/{sale_id}/receipt",
    response_model=ReceiptOut,
    summary="Preview the rendered receipt (read-only, no state change)",
)
async def preview_receipt(sale_id: uuid.UUID, service: SaleServiceDep) -> ReceiptOut:
    return await service.preview_receipt(sale_id)


@router.post(
    "/{sale_id}/receipt/regenerate",
    response_model=ReceiptOut,
    summary="Re-issue the receipt number and return the fresh receipt",
)
async def regenerate_receipt(sale_id: uuid.UUID, service: SaleServiceDep) -> ReceiptOut:
    return await service.regenerate_receipt(sale_id)


@router.post(
    "/{sale_id}/share-receipt",
    response_model=ReceiptOut,
    summary="Record that a receipt was shared, and to whom — optionally building a WhatsApp link",
)
async def share_receipt(
    sale_id: uuid.UUID,
    payload: SaleReceiptShareRequest,
    service: SaleServiceDep,
) -> ReceiptOut:
    return await service.share_receipt(
        sale_id, payload.shared_to, destination_number=payload.destination_number
    )
