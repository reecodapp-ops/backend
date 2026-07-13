"""Customer payment service.

TRIGGER-BASED ARCHITECTURE (Strategy A)
---------------------------------------
This service inserts ONLY the customer_payments row. The trigger
fn_after_payment_insert performs the oldest-first allocation, creates
payment_allocations, updates each debt-ledger row and the linked sale's
outstanding_amount, and adjusts customer/business cached balances. The service
never writes those — doing so would double-count.

Service responsibilities (what the trigger cannot do):
  1. Validate the customer exists and belongs to the business.
  2. Detect overpayment (amount > customer.debt_balance) and return a soft
     OVERPAYMENT warning — never block. The excess stays unallocated; the
     trigger allocates only up to each ledger's remaining amount, so the surplus
     simply isn't allocated (it raises cash and reduces debt to zero, which the
     trigger's GREATEST(0, ...) guards already handle).
  3. Read back the trigger-created allocations and the refreshed debt balance for
     the response, and report the unallocated portion.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.core.business_context import BusinessContext
from app.core.exceptions import ValidationError
from app.core.idempotency import get_cached_response, store_response
from app.repositories.payment_repository import PaymentRepository
from app.schemas.payment import (
    AllocationOut,
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentListOut,
    PaymentOut,
    PaymentWarning,
)


_IDEMPOTENCY_ENDPOINT = "POST /customer-payments"


class PaymentService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = PaymentRepository(ctx.session)

    async def create_payment(
        self, data: PaymentCreateRequest, *, idempotency_key: str | None = None
    ) -> PaymentCreateResponse:
        biz_id = self._ctx.business_id

        cached = await get_cached_response(
            self._ctx.session, biz_id, idempotency_key, _IDEMPOTENCY_ENDPOINT
        )
        if cached is not None:
            return PaymentCreateResponse.model_validate(cached)

        # 1. Validate customer.
        customer = await self._repo.get_customer(biz_id, data.customer_id)
        if customer is None:
            raise ValidationError(
                "Customer not found for this business.", code="invalid_customer"
            )

        # 2. Capture the debt balance BEFORE the payment so we can detect
        #    overpayment and compute the unallocated portion accurately.
        debt_before = customer.debt_balance or Decimal("0")

        warnings: list[PaymentWarning] = []
        unallocated = Decimal("0.00")
        if data.amount > debt_before:
            unallocated = (data.amount - debt_before).quantize(Decimal("0.01"))
            warnings.append(
                PaymentWarning(
                    code="OVERPAYMENT",
                    message=(
                        f"Payment of {data.amount} exceeds the customer's "
                        f"outstanding debt of {debt_before}. "
                        f"{unallocated} is unallocated."
                    ),
                )
            )

        occurred_at = data.occurred_at or datetime.now(timezone.utc)

        # 3. Insert the raw payment row — the trigger does the allocation.
        payment = await self._repo.insert_payment(
            business_id=biz_id,
            customer_id=data.customer_id,
            amount=data.amount,
            occurred_at=occurred_at,
            note=data.note,
        )

        # Ensure trigger work is flushed, then read results back.
        await self._ctx.session.flush()

        allocation_rows = await self._repo.get_allocations_for_payment(
            biz_id, payment.id
        )
        balance_after = await self._repo.get_customer_debt_balance(
            biz_id, data.customer_id
        )

        allocations = [
            AllocationOut(
                debt_ledger_id=alloc.debt_ledger_id,
                sale_id=ledger.sale_id,
                allocated_amount=alloc.allocated_amount,
                ledger_status=ledger.status,
                ledger_remaining=(
                    ledger.remaining_amount
                    if ledger.remaining_amount is not None
                    else (ledger.original_amount - ledger.paid_amount)
                ),
            )
            for alloc, ledger in allocation_rows
        ]

        response = PaymentCreateResponse(
            payment=PaymentOut.model_validate(payment),
            allocations=allocations,
            customer_debt_balance_after=balance_after,
            unallocated_amount=unallocated,
            warnings=warnings,
        )
        await store_response(
            self._ctx.session,
            biz_id,
            idempotency_key,
            _IDEMPOTENCY_ENDPOINT,
            response.model_dump(mode="json"),
        )
        return response

    async def list_payments(
        self,
        *,
        customer_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> PaymentListOut:
        rows, total = await self._repo.list_payments(
            business_id=self._ctx.business_id,
            customer_id=customer_id,
            limit=limit,
            offset=offset,
        )
        return PaymentListOut(
            items=[PaymentOut.model_validate(p) for p in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
