"""Pydantic v2 schemas for the Capital Transaction module.

The Sales/Expense/Stock modules all use endpoint-driven payment_type or category
codes. Capital is the same: a free-form ``type_code`` (MONEY_IN / MONEY_OUT /
PERSONAL_USE) discriminates direction. OPENING_BALANCE is deliberately NOT
acceptable here — it would double-count with the businesses.opening_balance
column maintained at onboarding (see business_service.py for the rationale).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CapitalTypeCode = Literal["MONEY_IN", "MONEY_OUT", "PERSONAL_USE"]
CapitalTypeFilter = Literal["MONEY_IN", "MONEY_OUT", "PERSONAL_USE", "OPENING_BALANCE"]


class CapitalTransactionCreateRequest(BaseModel):
    type_code: CapitalTypeCode = Field(
        ...,
        description=(
            "MONEY_IN raises cash; MONEY_OUT and PERSONAL_USE reduce it. "
            "OPENING_BALANCE is recorded only at business onboarding and cannot "
            "be created through this endpoint."
        ),
    )
    source_type_id: int | None = Field(
        default=None,
        ge=1,
        description="Origin of injected money. Only meaningful for MONEY_IN.",
    )
    amount: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    occurred_at: datetime | None = Field(default=None)
    note: str | None = Field(default=None)


class CapitalSourceTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    label: str


class CapitalTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    type_id: int
    type_code: str
    direction: str
    source_type_id: int | None
    amount: Decimal
    occurred_at: datetime
    note: str | None
    is_correction: bool
    created_at: datetime


class CapitalTransactionCreateResponse(BaseModel):
    capital_transaction: CapitalTransactionOut


class CapitalTransactionListOut(BaseModel):
    items: list[CapitalTransactionOut]
    total: int
    limit: int
    offset: int


__all__ = [
    "CapitalTypeCode",
    "CapitalTypeFilter",
    "CapitalTransactionCreateRequest",
    "CapitalSourceTypeOut",
    "CapitalTransactionOut",
    "CapitalTransactionCreateResponse",
    "CapitalTransactionListOut",
]
