"""Pydantic v2 schemas for the Reecod Customer & Supplier Scoring engine.

Implements the API shape from the "Reecod Engine Logic Spec — Customer &
Supplier Scoring V1/V2" document exactly: one ``ScoreOut`` shape per lens
(trust/value for customers, reliability/terms for suppliers), a single
``active_view`` pointer per the spec's "Single Lens UI Rule" (§1.3 — the
frontend shows one big score at a time; the backend still returns every
lens so switching tabs doesn't need another request), and a strict
``percentage: null, status: "insufficient_data"`` shape when the Data
Readiness Gate (§1.1) isn't met — never a fabricated number.

Product copy (§8): only the exact labels from the spec's color-mapping
tables are ever used — see ``ScoreLabel`` for the closed set.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ScoreStatus = Literal["calculated", "insufficient_data"]
ScoreColor = Literal["green", "yellow", "orange", "red"]

# Closed set of approved labels (spec §2, §8) — never invent new copy here.
ScoreLabel = Literal[
    # Customer Credit Trust
    "Trusted", "Watch Carefully", "Limit Credit", "Cash Only",
    # Buyer Value
    "Top Buyer", "Good Buyer", "Occasional Buyer", "Low Activity",
    # Supplier Reliability
    "Reliable", "Fair", "Unstable", "Avoid for Urgent Stock",
    # Supplier Terms
    "Strong Terms", "Fair Terms", "Weak Terms", "Poor Terms",
    # Insufficient-data placeholders
    "New", "Needs More Data", "Needs More Records",
]

CustomerViewMode = Literal["trust", "value"]
SupplierViewMode = Literal["reliability", "terms"]


class ScoreOut(BaseModel):
    """One lens of one entity's score.

    ``percentage`` and ``color`` are ``None`` whenever ``status`` is
    ``"insufficient_data"`` — the spec is explicit that the engine must never
    fake a number when the data isn't there (§1.1, §1.2, §7.1).
    """

    percentage: int | None = Field(default=None, ge=0, le=100)
    color: ScoreColor | None = None
    label: ScoreLabel
    status: ScoreStatus
    meaning: str
    action: str
    # Present only for insufficient_data — a short "why", e.g.
    # "Needs 3 credit records to calculate." (§1.1 frontend display rule).
    reason: str | None = None
    # Raw factor breakdown for anyone who wants to show/debug the math —
    # never required by the frontend, always safe to ignore.
    factors: dict[str, Any] | None = None


class CustomerScoresOut(BaseModel):
    """GET /customers/{id}/scores response — matches spec §5.2 exactly."""

    model_config = ConfigDict(from_attributes=False)

    customer_id: uuid.UUID
    name: str
    active_view: CustomerViewMode
    scores: dict[Literal["trust", "value"], ScoreOut]


class SupplierScoresOut(BaseModel):
    """GET /suppliers/{id}/scores response — matches spec §5.3 exactly."""

    model_config = ConfigDict(from_attributes=False)

    supplier_id: uuid.UUID
    name: str
    active_view: SupplierViewMode
    scores: dict[Literal["reliability", "terms"], ScoreOut]


__all__ = [
    "ScoreStatus",
    "ScoreColor",
    "ScoreLabel",
    "CustomerViewMode",
    "SupplierViewMode",
    "ScoreOut",
    "CustomerScoresOut",
    "SupplierScoresOut",
]
