"""Customer Payment router (per blueprint F8). Requires X-Business-Id header.

    POST /customer-payments      record a payment (oldest-first allocation by trigger)
    GET  /customer-payments      list payments (?customer_id=&limit=&offset=)

The allocation across the customer's open debt ledger entries is performed by
the database trigger fn_after_payment_insert; this endpoint only records the
raw payment.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, status

from app.core.master_data_deps import PaymentServiceDep
from app.schemas.payment import (
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentListOut,
)

router = APIRouter(prefix="/customer-payments", tags=["Customer Payments"])


@router.post(
    "",
    response_model=PaymentCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a customer payment (oldest-first allocation)",
)
async def create_payment(
    payload: PaymentCreateRequest,
    service: PaymentServiceDep,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=200,
            description=(
                "Optional client-generated key (a UUID is recommended). "
                "Replaying a POST with the same key within 24h returns the "
                "original response instead of recording a second payment."
            ),
        ),
    ] = None,
) -> PaymentCreateResponse:
    return await service.create_payment(payload, idempotency_key=idempotency_key)


@router.get(
    "",
    response_model=PaymentListOut,
    summary="List customer payments (optionally filtered by customer)",
)
async def list_payments(
    service: PaymentServiceDep,
    customer_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaymentListOut:
    return await service.list_payments(
        customer_id=customer_id, limit=limit, offset=offset
    )
