"""Pydantic v2 schemas for the Issue (Decision Engine) module."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    issue_type_id: int
    issue_type_code: str
    ref_product_id: uuid.UUID | None
    ref_customer_id: uuid.UUID | None
    ref_sale_id: uuid.UUID | None
    confidence_level: int
    severity: str
    headline: str
    body_text: str | None
    suggested_action: str | None
    data_snapshot: dict[str, Any] | None
    status: str
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IssueListOut(BaseModel):
    items: list[IssueOut]
    total: int
    limit: int
    offset: int


class IssueUpdateRequest(BaseModel):
    status: Literal["RESOLVED", "IGNORED"]


class RecomputeResponse(BaseModel):
    """What the recompute job did for this business."""

    created: int
    superseded: int
    unchanged: int


__all__ = [
    "IssueOut",
    "IssueListOut",
    "IssueUpdateRequest",
    "RecomputeResponse",
]
