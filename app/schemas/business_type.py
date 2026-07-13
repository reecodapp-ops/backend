"""Pydantic v2 schemas for the business_types lookup table."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BusinessTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    label: str
    icon_name: str | None
    sort_order: int
    is_active: bool


class BusinessTypeListResponse(BaseModel):
    business_types: list[BusinessTypeOut]


__all__ = ["BusinessTypeOut", "BusinessTypeListResponse"]