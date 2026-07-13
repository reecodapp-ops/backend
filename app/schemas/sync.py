"""Pydantic v2 schemas for the Sync module.

POST /sync receives a batch of client-generated events with stable UUIDs and
returns the server's acceptance verdict per event plus refreshed aggregates and
the latest issue cards. Idempotent: re-sending an event with the same client_id
returns DUPLICATE without re-executing the side effect.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.issue import IssueOut

SyncEventType = Literal[
    "SALE", "CUSTOMER_PAYMENT", "EXPENSE", "STOCK_PURCHASE", "CAPITAL_TRANSACTION"
]


class SyncEventIn(BaseModel):
    client_id: uuid.UUID = Field(
        ..., description="Stable UUID minted by the client; used as idempotency key."
    )
    event_type: SyncEventType
    payload: dict[str, Any] = Field(
        ..., description="The same body the per-event endpoint would accept."
    )
    client_created_at: datetime


class SyncBatchRequest(BaseModel):
    device_id: uuid.UUID | None = Field(
        default=None, description="Optional device this batch came from."
    )
    events: list[SyncEventIn] = Field(..., min_length=1, max_length=200)


class AcceptedEvent(BaseModel):
    client_id: uuid.UUID
    server_record_id: uuid.UUID | None = None


class RejectedEvent(BaseModel):
    client_id: uuid.UUID
    reason: str
    error_code: str | None = None


class StockSnapshot(BaseModel):
    product_id: uuid.UUID
    stock_on_hand: Decimal


class SyncAggregates(BaseModel):
    cash_on_hand: Decimal
    total_debt_out: Decimal
    stock: list[StockSnapshot] = []


class SyncBatchResponse(BaseModel):
    accepted: list[AcceptedEvent]
    rejected: list[RejectedEvent]
    duplicates: list[uuid.UUID] = []
    aggregates: SyncAggregates
    issues: list[IssueOut] = []


__all__ = [
    "SyncEventType",
    "SyncEventIn",
    "SyncBatchRequest",
    "AcceptedEvent",
    "RejectedEvent",
    "StockSnapshot",
    "SyncAggregates",
    "SyncBatchResponse",
]
