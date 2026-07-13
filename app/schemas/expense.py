"""Pydantic v2 schemas for the Expense module."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExpenseCreateRequest(BaseModel):
    category_id: int = Field(..., ge=1)
    amount: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    occurred_at: datetime | None = Field(default=None)
    note: str | None = Field(default=None)
    supplier_name: str | None = Field(default=None, max_length=200)

    @field_validator("supplier_name")
    @classmethod
    def _strip_supplier(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class ExpenseCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: int
    code: str
    label: str
    icon_name: str | None
    is_system: bool
    is_active: bool


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    category_id: int
    amount: Decimal
    occurred_at: datetime
    note: str | None
    supplier_name: str | None
    is_correction: bool
    created_at: datetime


class ExpenseWarning(BaseModel):
    code: str
    message: str


class ExpenseCreateResponse(BaseModel):
    expense: ExpenseOut
    warnings: list[ExpenseWarning] = []


class ExpenseListOut(BaseModel):
    items: list[ExpenseOut]
    total: int
    limit: int
    offset: int


__all__ = [
    "ExpenseCreateRequest",
    "ExpenseCategoryOut",
    "ExpenseOut",
    "ExpenseWarning",
    "ExpenseCreateResponse",
    "ExpenseListOut",
]
