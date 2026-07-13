"""Pydantic v2 schemas for the Business Insights module.

One flat, uniform shape (``InsightOut``) for every kind of recommendation or
warning, so the frontend can render them all with a single component and the
backend can add new insight categories later without a schema migration —
``category`` is a plain string-backed enum and ``data`` is a free-form JSON
payload for anything category-specific (e.g. the actual product id/name
behind a "slow-moving product" insight).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

InsightCategory = Literal[
    "BEST_SELLING_PRODUCTS",
    "SLOW_MOVING_PRODUCTS",
    "LOW_STOCK_PRODUCTS",
    "OVERDUE_DEBT",
    "HIGH_RISK_CUSTOMERS",
    "SALES_TREND",
    "EXPENSE_PRESSURE",
    "CASH_FLOW",
    "UNUSUAL_ACTIVITY",
]

InsightSeverity = Literal["INFO", "WARN", "ALERT"]


class InsightOut(BaseModel):
    """One human-readable recommendation or warning.

    ``title`` is a short headline suitable for a card/notification;
    ``message`` is the full sentence explaining it; ``data`` carries the
    numbers/ids behind it for a frontend that wants to deep-link or chart
    rather than just display text.
    """

    model_config = ConfigDict(from_attributes=True)

    category: InsightCategory
    severity: InsightSeverity
    title: str
    message: str
    data: dict[str, Any] | None = None


class InsightsListOut(BaseModel):
    """GET /businesses/{business_id}/insights response.

    ``items`` is intentionally unpaginated — this endpoint returns a small,
    bounded set of the most relevant insights (each generator caps itself,
    typically at 3-5 items), not a browsable list.
    """

    business_id: uuid.UUID
    generated_at: datetime
    items: list[InsightOut] = Field(default_factory=list)


__all__ = [
    "InsightCategory",
    "InsightSeverity",
    "InsightOut",
    "InsightsListOut",
]
