"""Pydantic v2 schemas for the Product Category and Product modules."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
ProductStatus = Literal["ACTIVE", "ARCHIVED"]
StockStatus = Literal["OUT_OF_STOCK", "LOW_STOCK", "SLOW_MOVER", "OK"]


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #
class CategoryCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    icon_name: str | None = Field(default=None, max_length=100)
    color_hex: str | None = Field(default=None, max_length=7)
    sort_order: int = Field(default=0, ge=0)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v

    @field_validator("color_hex")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not HEX_RE.match(v):
            raise ValueError("color_hex must be a hex color like #4CAF50")
        return v


class CategoryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    icon_name: str | None = Field(default=None, max_length=100)
    color_hex: str | None = Field(default=None, max_length=7)
    sort_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = Field(default=None)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v

    @field_validator("color_hex")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not HEX_RE.match(v):
            raise ValueError("color_hex must be a hex color like #4CAF50")
        return v


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    business_id: uuid.UUID | None
    name: str
    icon_name: str | None
    color_hex: str | None
    sort_order: int
    is_system: bool
    is_active: bool
    created_at: datetime


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #
class ProductCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    category_id: int | None = Field(default=None, ge=1)
    unit: str | None = Field(default=None, max_length=50)
    selling_price: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    buying_price: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    stock_qty: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=4)
    low_stock_level: Decimal = Field(default=Decimal("5"), ge=0, max_digits=15, decimal_places=4)
    image_url: str | None = Field(default=None, max_length=500)
    image_thumbnail_url: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v


class ProductUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    category_id: int | None = Field(default=None, ge=1)
    unit: str | None = Field(default=None, max_length=50)
    selling_price: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    buying_price: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    low_stock_level: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=4)
    image_url: str | None = Field(default=None, max_length=500)
    image_thumbnail_url: str | None = Field(default=None, max_length=500)
    status: ProductStatus | None = Field(default=None)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    category_id: int | None
    name: str
    unit: str | None
    selling_price: Decimal | None
    buying_price: Decimal | None
    stock_on_hand: Decimal
    low_stock_level: Decimal
    avg_buying_cost: Decimal | None
    image_url: str | None
    image_thumbnail_url: str | None
    status: str
    last_sold_at: datetime | None
    last_restocked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProductWarning(BaseModel):
    code: str
    message: str


class ProductCreateResponse(BaseModel):
    """Product plus any soft warnings (e.g. possible duplicate item name).

    Per the blueprint's Validation-First rule, soft warnings never block the
    write; they are returned alongside the created/updated resource.
    """

    product: ProductOut
    warnings: list[ProductWarning] = []


class ProductListOut(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int


__all__ = [
    "CategoryCreateRequest",
    "CategoryUpdateRequest",
    "CategoryOut",
    "ProductCreateRequest",
    "ProductUpdateRequest",
    "ProductOut",
    "ProductCreateResponse",
    "ProductWarning",
    "ProductListOut",
    "ProductStatus",
    "StockStatus",
]
