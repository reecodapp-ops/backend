"""Pydantic v2 schemas for the Stock Purchase module."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StockPurchaseItemCreate(BaseModel):
    product_id: uuid.UUID | None = Field(
        default=None,
        description="Existing product. Null = ad-hoc line not linked to inventory.",
    )
    product_name: str = Field(..., min_length=1, max_length=300)
    quantity: Decimal = Field(..., gt=0, max_digits=15, decimal_places=4, description="Quantity actually received.")
    quantity_ordered: Decimal | None = Field(
        default=None, gt=0, max_digits=15, decimal_places=4,
        description=(
            "Quantity originally ordered, if different from what was received "
            "(Reecod Supplier Reliability Score's Order Fill Rate input). "
            "Omit if you didn't track ordered vs. received separately -- the "
            "line is then treated as fully filled."
        ),
    )
    total_item_cost: Decimal = Field(..., ge=0, max_digits=15, decimal_places=2)

    @field_validator("product_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("product_name cannot be empty")
        return v


class StockPurchaseCreateRequest(BaseModel):
    supplier_id: uuid.UUID | None = Field(default=None)
    supplier_name_free: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Free-text supplier name when no supplier_id is supplied; "
            "matches Figma 'Supplier name (Optional)'."
        ),
    )
    occurred_at: datetime | None = Field(default=None)
    promised_delivery_date: date | None = Field(
        default=None,
        description="What the supplier promised, if recorded (Reecod Supplier Reliability Score input).",
    )
    actual_delivery_date: date | None = Field(
        default=None,
        description="When it actually arrived, if different from occurred_at.",
    )
    note: str | None = Field(default=None)
    items: list[StockPurchaseItemCreate] = Field(..., min_length=1)


class StockPurchaseItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID | None
    product_name_snapshot: str
    quantity: Decimal
    quantity_ordered: Decimal | None
    unit_buying_cost: Decimal | None
    total_item_cost: Decimal


class StockPurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    supplier_id: uuid.UUID | None
    supplier_name_free: str | None
    total_buying_cost: Decimal
    occurred_at: datetime
    promised_delivery_date: date | None
    actual_delivery_date: date | None
    note: str | None
    is_correction: bool
    created_at: datetime
    items: list[StockPurchaseItemOut]


class StockPurchaseWarning(BaseModel):
    code: str
    message: str


class StockPurchaseCreateResponse(BaseModel):
    purchase: StockPurchaseOut
    warnings: list[StockPurchaseWarning] = []


class StockPurchaseListOut(BaseModel):
    items: list[StockPurchaseOut]
    total: int
    limit: int
    offset: int


__all__ = [
    "StockPurchaseItemCreate",
    "StockPurchaseCreateRequest",
    "StockPurchaseItemOut",
    "StockPurchaseOut",
    "StockPurchaseWarning",
    "StockPurchaseCreateResponse",
    "StockPurchaseListOut",
]
