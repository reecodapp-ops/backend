"""Pydantic v2 schemas for the Customer Payment module."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PaymentCreateRequest(BaseModel):
    customer_id: uuid.UUID
    amount: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    occurred_at: datetime | None = Field(
        default=None, description="Defaults to server time; may be backdated."
    )
    note: str | None = Field(default=None)


class AllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    debt_ledger_id: uuid.UUID
    sale_id: uuid.UUID
    allocated_amount: Decimal
    ledger_status: str
    ledger_remaining: Decimal


class PaymentWarning(BaseModel):
    code: str
    message: str


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    customer_id: uuid.UUID
    amount: Decimal
    occurred_at: datetime
    note: str | None
    is_correction: bool
    created_at: datetime


class PaymentCreateResponse(BaseModel):
    """The recorded payment, the trigger-created allocations, the refreshed
    customer debt balance, and any soft warnings (e.g. overpayment)."""

    payment: PaymentOut
    allocations: list[AllocationOut]
    customer_debt_balance_after: Decimal
    unallocated_amount: Decimal
    warnings: list[PaymentWarning] = []


class PaymentListOut(BaseModel):
    items: list[PaymentOut]
    total: int
    limit: int
    offset: int


__all__ = [
    "PaymentCreateRequest",
    "AllocationOut",
    "PaymentWarning",
    "PaymentOut",
    "PaymentCreateResponse",
    "PaymentListOut",
]
