"""Sync repository.

Looks up existing sync_events by (business_id, client_id) for idempotency,
inserts new ones, and provides the aggregate read-back the response needs.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.product import Product
from app.models.sync import SyncEvent


class SyncRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_existing_events(
        self, business_id: uuid.UUID, client_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, SyncEvent]:
        if not client_ids:
            return {}
        result = await self._session.execute(
            select(SyncEvent).where(
                SyncEvent.business_id == business_id,
                SyncEvent.client_id.in_(client_ids),
            )
        )
        return {e.client_id: e for e in result.scalars().all()}

    async def insert_event(
        self,
        *,
        business_id: uuid.UUID,
        device_id: uuid.UUID | None,
        client_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        client_created_at: datetime,
        status: str,
        server_record_id: uuid.UUID | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> SyncEvent:
        event = SyncEvent(
            business_id=business_id,
            device_id=device_id,
            client_id=client_id,
            event_type=event_type,
            payload=payload,
            client_created_at=client_created_at,
            status=status,
            server_record_id=server_record_id,
            error_code=error_code,
            error_message=error_message,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def get_business_aggregates(
        self, business_id: uuid.UUID
    ) -> tuple[Decimal, Decimal]:
        result = await self._session.execute(
            select(Business.cash_on_hand, Business.total_debt_out).where(
                Business.id == business_id
            )
        )
        row = result.first()
        if row is None:
            return Decimal("0"), Decimal("0")
        return row[0], row[1]

    async def get_active_stock_snapshot(
        self, business_id: uuid.UUID, product_ids: list[uuid.UUID]
    ) -> list[tuple[uuid.UUID, Decimal]]:
        if not product_ids:
            return []
        result = await self._session.execute(
            select(Product.id, Product.stock_on_hand).where(
                Product.business_id == business_id,
                Product.id.in_(product_ids),
            )
        )
        return [(pid, qty) for pid, qty in result.all()]

    async def flush(self) -> None:
        await self._session.flush()
