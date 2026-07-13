"""Pydantic v2 schemas for the Correction module.

The supported correction kinds and their ``new_values`` shapes:

  SALE_EDIT       : { total_amount: Decimal }              — replace a sale's total
                                                              (line-by-line not edited;
                                                              SALE_ITEM_EDIT is out of scope)
  EXPENSE_EDIT    : { amount: Decimal, category_id?: int,  — replace an expense
                      note?: str, supplier_name?: str }
  PAYMENT_EDIT    : { amount: Decimal, note?: str }         — replace a customer payment
  CAPITAL_EDIT    : { amount: Decimal, note?: str,         — replace a capital transaction
                      source_type_id?: int }
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CorrectionTypeCode = Literal[
    "SALE_EDIT", "EXPENSE_EDIT", "PAYMENT_EDIT", "CAPITAL_EDIT"
]


class CorrectionNewValues(BaseModel):
    """Generic new_values envelope; the service validates the shape per type."""

    amount: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    total_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=15, decimal_places=2
    )
    category_id: int | None = Field(default=None, ge=1)
    source_type_id: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None)
    supplier_name: str | None = Field(default=None, max_length=200)


class CorrectionCreateRequest(BaseModel):
    correction_type_code: CorrectionTypeCode
    original_record_id: uuid.UUID
    reason: str = Field(..., min_length=1, max_length=2000)
    new_values: CorrectionNewValues


class CorrectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    correction_type_id: int
    correction_type_code: str
    original_record_id: uuid.UUID
    corrected_record_id: uuid.UUID | None
    field_changed: str | None
    old_value_text: str | None
    new_value_text: str | None
    reason: str | None
    created_at: datetime


class CorrectionCreateResponse(BaseModel):
    correction: CorrectionOut
    corrected_record_id: uuid.UUID
    cash_on_hand_after: Decimal | None = None
    total_debt_out_after: Decimal | None = None


class CorrectionListOut(BaseModel):
    items: list[CorrectionOut]
    total: int
    limit: int
    offset: int


__all__ = [
    "CorrectionTypeCode",
    "CorrectionNewValues",
    "CorrectionCreateRequest",
    "CorrectionOut",
    "CorrectionCreateResponse",
    "CorrectionListOut",
]
