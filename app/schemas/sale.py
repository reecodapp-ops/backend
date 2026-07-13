"""Pydantic v2 schemas for the Sales module.

POST /sales accepts a cash or debt sale with one or more line items. The
response returns the persisted sale (with trigger-refreshed values) plus any
soft warnings (oversell, duplicate products).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PaymentTypeCode = Literal["CASH", "ON_DEBT"]
ReceiptSharedTo = Literal["CUSTOMER", "BUSINESS", "OTHER"]


class SaleItemCreate(BaseModel):
    product_id: uuid.UUID | None = Field(
        default=None,
        description="Existing product. Null for an ad-hoc line (quick sale).",
    )
    product_name: str = Field(..., min_length=1, max_length=300)
    quantity: Decimal = Field(..., gt=0, max_digits=15, decimal_places=4)
    unit_price: Decimal = Field(..., ge=0, max_digits=15, decimal_places=2)

    @field_validator("product_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("product_name cannot be empty")
        return v


class SaleCreateRequest(BaseModel):
    payment_type: PaymentTypeCode
    customer_id: uuid.UUID | None = Field(
        default=None, description="Required when payment_type is ON_DEBT."
    )
    occurred_at: datetime | None = Field(
        default=None, description="Defaults to server time; may be backdated."
    )
    due_date: date | None = Field(
        default=None,
        description=(
            "Optional owner-set due date, only meaningful for ON_DEBT sales. "
            "Copied onto the created customer_debt_ledger row and feeds the "
            "Customer Credibility Engine's overdue detection."
        ),
    )
    note: str | None = Field(default=None)
    items: list[SaleItemCreate] = Field(..., min_length=1)

    @field_validator("items")
    @classmethod
    def _non_empty(cls, v: list[SaleItemCreate]) -> list[SaleItemCreate]:
        if not v:
            raise ValueError("a sale must have at least one item")
        return v


class SaleItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID | None
    product_name_snapshot: str
    quantity: Decimal
    unit_price: Decimal
    buying_cost: Decimal | None
    line_total: Decimal
    line_margin: Decimal


class SaleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    customer_id: uuid.UUID | None
    payment_type_id: int
    total_amount: Decimal
    total_buying_cost: Decimal
    gross_margin: Decimal
    outstanding_amount: Decimal
    due_date: date | None
    occurred_at: datetime
    note: str | None
    receipt_number: str | None
    receipt_shared_at: datetime | None
    receipt_shared_to: ReceiptSharedTo | None
    is_correction: bool
    created_at: datetime
    items: list[SaleItemOut]


class SaleReceiptShareRequest(BaseModel):
    """POST /sales/{id}/share-receipt — records who a receipt was shared to,
    and optionally the destination phone number used for a WhatsApp share.

    ``destination_number`` is NOT persisted (the schema has no column for it —
    see ReceiptOut.whatsapp_share_url); it is used only to build the WhatsApp
    click-to-chat link returned in the response.
    """

    shared_to: ReceiptSharedTo
    destination_number: str | None = Field(
        default=None,
        max_length=20,
        description="Optional phone number (E.164-ish) to build a WhatsApp share link for.",
    )


class ReceiptLineOut(BaseModel):
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


class ReceiptOut(BaseModel):
    """A fully rendered receipt — preview, regeneration, and the response to
    a WhatsApp share all return this same shape, so the frontend has one
    renderer for all three.

    ``footer`` is built from the business's own profile (shop name, phone,
    location, currency) plus a fixed thank-you line, since there is no
    per-business custom-footer column in the schema. See MIGRATION_NOTES.md.
    """

    sale_id: uuid.UUID
    receipt_number: str | None
    customer_id: uuid.UUID | None
    due_date: date | None
    business_name: str
    business_phone: str | None
    business_location: str | None
    currency_symbol: str
    customer_name: str | None
    payment_type: PaymentTypeCode
    occurred_at: datetime
    items: list[ReceiptLineOut]
    total_amount: Decimal
    outstanding_amount: Decimal
    footer: str
    is_correction: bool
    original_sale_id: uuid.UUID | None
    receipt_shared_at: datetime | None
    receipt_shared_to: ReceiptSharedTo | None
    whatsapp_share_url: str | None = Field(
        default=None,
        description="Click-to-chat wa.me link, present only when a share was requested with a destination number.",
    )


class SaleWarning(BaseModel):
    code: str
    message: str


class SaleCreateResponse(BaseModel):
    """Persisted sale plus any soft warnings (Validation-First: never blocking)."""

    sale: SaleOut
    warnings: list[SaleWarning] = []


class SaleListResponse(BaseModel):
    """Paginated list of sales, e.g. for GET /sales and GET /sales/today."""

    sales: list[SaleOut]
    total: int
    limit: int
    offset: int


__all__ = [
    "PaymentTypeCode",
    "ReceiptSharedTo",
    "SaleItemCreate",
    "SaleCreateRequest",
    "SaleItemOut",
    "SaleOut",
    "SaleReceiptShareRequest",
    "ReceiptLineOut",
    "ReceiptOut",
    "SaleWarning",
    "SaleCreateResponse",
    "SaleListResponse",
]