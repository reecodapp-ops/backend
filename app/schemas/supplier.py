"""Pydantic v2 schemas for the Supplier module."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.phone_fields import PhonePairFields

PHONE_RE = re.compile(r"^\+\d{8,15}$")
SupplierStatus = Literal["ACTIVE", "ARCHIVED"]

# [NEW -- Reecod Scoring Engine] Supplier Terms Score input (spec section 4.2).
PaymentFlexibility = Literal["FLEXIBLE", "SOMETIMES_FLEXIBLE", "STRICT_CASH", "UNKNOWN"]

# [NEW -- Supplier & Product Round] GET /suppliers's `reliability_level`
# filter -- maps 1:1 to the Reliability Score's four tiers (spec section 2.3).
ReliabilityLevel = Literal["RELIABLE", "FAIR", "UNSTABLE", "AVOID_URGENT"]
SupplierOrder = Literal["name_asc", "recent", "reliability_desc", "terms_desc"]


def _normalize_optional_phone(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().replace(" ", "")
    if v == "":
        return None
    if not PHONE_RE.match(v):
        raise ValueError("phone_number must be international format, e.g. +256700000000")
    return v


class SupplierCreateRequest(PhonePairFields):
    name: str = Field(..., min_length=1, max_length=200)
    phone_number: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None)
    credit_terms_days: int | None = Field(
        default=None, ge=0, le=365,
        description="Days of credit this supplier offers (Reecod Supplier Terms Score input).",
    )
    payment_flexibility: PaymentFlexibility | None = Field(default=None)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _normalize_optional_phone(v)


class SupplierUpdateRequest(PhonePairFields):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone_number: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None)
    status: SupplierStatus | None = Field(default=None)
    credit_terms_days: int | None = Field(default=None, ge=0, le=365)
    payment_flexibility: PaymentFlexibility | None = Field(default=None)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _normalize_optional_phone(v)


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    name: str
    phone_number: str | None
    notes: str | None
    credit_terms_days: int | None
    payment_flexibility: PaymentFlexibility | None
    # [NEW -- Supplier & Product Round] Reliability badge, computed the same
    # way as GET /suppliers/{id}/scores's `reliability` lens, batched across
    # the whole list (never one query per row -- see ScoringService
    # .reliability_badges_bulk). `reliability_label` is null with
    # `reliability_color: "gray"` when the supplier hasn't cleared the
    # data-readiness gate yet (< 3 clean supplier orders) -- frontend renders
    # "Needs more records".
    reliability_label: str | None = None
    reliability_color: str = "gray"
    status: str
    created_at: datetime
    updated_at: datetime


class SupplierListOut(BaseModel):
    items: list[SupplierOut]
    total: int
    limit: int
    offset: int


__all__ = [
    "SupplierCreateRequest",
    "SupplierUpdateRequest",
    "SupplierOut",
    "SupplierListOut",
    "SupplierStatus",
    "PaymentFlexibility",
    "ReliabilityLevel",
    "SupplierOrder",
]
