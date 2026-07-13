"""Sync service — offline batch ingest.

Per blueprint F16:
  * Idempotent on client UUID — duplicates returned as DUPLICATE without
    re-executing the side effect.
  * Process events in client_created_at order.
  * Wrap each event in its own savepoint so one failure doesn't roll back the
    whole batch.
  * Return refreshed aggregates + new issues (the "pull").

This service dispatches to the existing per-module services (SaleService,
PaymentService, etc.) so a sync'd event takes the same code path as a live
single-event POST.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.core.business_context import BusinessContext
from app.core.exceptions import AppError, ValidationError as AppValidationError
from app.repositories.sync_repository import SyncRepository
from app.schemas.capital import CapitalTransactionCreateRequest
from app.schemas.expense import ExpenseCreateRequest
from app.schemas.issue import IssueOut
from app.schemas.payment import PaymentCreateRequest
from app.schemas.sale import SaleCreateRequest
from app.schemas.stock_purchase import StockPurchaseCreateRequest
from app.schemas.sync import (
    AcceptedEvent,
    RejectedEvent,
    StockSnapshot,
    SyncAggregates,
    SyncBatchRequest,
    SyncBatchResponse,
    SyncEventIn,
)
from app.services.capital_service import CapitalService
from app.services.expense_service import ExpenseService
from app.services.issue_service import IssueService
from app.services.payment_service import PaymentService
from app.services.sale_service import SaleService
from app.services.stock_purchase_service import StockPurchaseService


_EVENT_HANDLERS: dict[str, tuple] = {
    "SALE": (SaleCreateRequest, "sale", SaleService, "create_sale", "sale"),
    "CUSTOMER_PAYMENT": (
        PaymentCreateRequest, "payment", PaymentService, "create_payment", "payment",
    ),
    "EXPENSE": (
        ExpenseCreateRequest, "expense", ExpenseService, "create_expense", "expense",
    ),
    "STOCK_PURCHASE": (
        StockPurchaseCreateRequest, "purchase", StockPurchaseService,
        "create_purchase", "purchase",
    ),
    "CAPITAL_TRANSACTION": (
        CapitalTransactionCreateRequest, "capital_transaction", CapitalService,
        "create_capital_transaction", "capital_transaction",
    ),
}


class SyncService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = SyncRepository(ctx.session)

    async def process_batch(self, batch: SyncBatchRequest) -> SyncBatchResponse:
        biz_id = self._ctx.business_id
        session = self._ctx.session

        # 1. Idempotency: pull existing rows for these client_ids.
        client_ids = [e.client_id for e in batch.events]
        existing = await self._repo.get_existing_events(biz_id, client_ids)

        accepted: list[AcceptedEvent] = []
        rejected: list[RejectedEvent] = []
        duplicates: list[uuid.UUID] = []
        touched_product_ids: set[uuid.UUID] = set()

        # 2. Process in client_created_at order.
        ordered = sorted(batch.events, key=lambda e: e.client_created_at)

        for event in ordered:
            if event.client_id in existing:
                prior = existing[event.client_id]
                duplicates.append(event.client_id)
                if prior.status == "ACCEPTED":
                    accepted.append(
                        AcceptedEvent(
                            client_id=event.client_id,
                            server_record_id=prior.server_record_id,
                        )
                    )
                else:
                    rejected.append(
                        RejectedEvent(
                            client_id=event.client_id,
                            reason=prior.error_message or "Previously rejected",
                            error_code=prior.error_code,
                        )
                    )
                continue

            # 3. Per-event savepoint.
            savepoint = await session.begin_nested()
            try:
                outcome = await self._dispatch_event(event, batch.device_id)
                await savepoint.commit()
                accepted.append(
                    AcceptedEvent(
                        client_id=event.client_id,
                        server_record_id=outcome["server_record_id"],
                    )
                )
                if outcome.get("touched_product_ids"):
                    touched_product_ids.update(outcome["touched_product_ids"])
            except AppError as exc:
                await savepoint.rollback()
                await self._repo.insert_event(
                    business_id=biz_id,
                    device_id=batch.device_id,
                    client_id=event.client_id,
                    event_type=event.event_type,
                    payload=event.payload,
                    client_created_at=event.client_created_at,
                    status="REJECTED",
                    error_code=exc.code,
                    error_message=exc.message,
                )
                rejected.append(
                    RejectedEvent(
                        client_id=event.client_id,
                        reason=exc.message,
                        error_code=exc.code,
                    )
                )
            except PydanticValidationError as exc:
                await savepoint.rollback()
                await self._repo.insert_event(
                    business_id=biz_id,
                    device_id=batch.device_id,
                    client_id=event.client_id,
                    event_type=event.event_type,
                    payload=event.payload,
                    client_created_at=event.client_created_at,
                    status="REJECTED",
                    error_code="invalid_payload",
                    error_message=str(exc),
                )
                rejected.append(
                    RejectedEvent(
                        client_id=event.client_id,
                        reason="Payload failed validation.",
                        error_code="invalid_payload",
                    )
                )
            except Exception as exc:  # pragma: no cover — defensive
                await savepoint.rollback()
                await self._repo.insert_event(
                    business_id=biz_id,
                    device_id=batch.device_id,
                    client_id=event.client_id,
                    event_type=event.event_type,
                    payload=event.payload,
                    client_created_at=event.client_created_at,
                    status="REJECTED",
                    error_code="server_error",
                    error_message=str(exc),
                )
                rejected.append(
                    RejectedEvent(
                        client_id=event.client_id,
                        reason="Server error processing this event.",
                        error_code="server_error",
                    )
                )

        # 4. Refreshed aggregates + the latest issue cards.
        await session.flush()
        cash, debt = await self._repo.get_business_aggregates(biz_id)
        stock_snap = []
        if touched_product_ids:
            rows = await self._repo.get_active_stock_snapshot(
                biz_id, list(touched_product_ids)
            )
            stock_snap = [
                StockSnapshot(product_id=pid, stock_on_hand=qty)
                for pid, qty in rows
            ]

        issue_service = IssueService(self._ctx)
        issues_listing = await issue_service.list_issues(
            status="ACTIVE",
            min_confidence=None,
            severity=None,
            limit=20,
            offset=0,
        )

        return SyncBatchResponse(
            accepted=accepted,
            rejected=rejected,
            duplicates=duplicates,
            aggregates=SyncAggregates(
                cash_on_hand=cash,
                total_debt_out=debt,
                stock=stock_snap,
            ),
            issues=issues_listing.items,
        )

    async def _dispatch_event(
        self, event: SyncEventIn, device_id: uuid.UUID | None
    ) -> dict[str, Any]:
        """Run the event's payload through the corresponding service.

        Returns ``{"server_record_id", "touched_product_ids"}`` so the batch
        loop can stitch the response together. Persists the matching sync_events
        row on success (the rejection path persists its own).
        """
        if event.event_type not in _EVENT_HANDLERS:
            raise AppValidationError(
                f"Unknown event_type '{event.event_type}'.",
                code="unknown_event_type",
            )
        request_cls, _, service_cls, method_name, response_attr = _EVENT_HANDLERS[
            event.event_type
        ]

        request_obj = request_cls.model_validate(event.payload)
        service = service_cls(self._ctx)
        response = await getattr(service, method_name)(request_obj)
        # Response objects are Pydantic models exposing the wrapped record.
        record = getattr(response, response_attr)
        server_record_id = getattr(record, "id")

        touched_product_ids: set[uuid.UUID] = set()
        # Sales and stock purchases impact stock per line; surface those.
        if event.event_type == "SALE":
            for item in getattr(record, "items", []) or []:
                if item.product_id is not None:
                    touched_product_ids.add(item.product_id)
        elif event.event_type == "STOCK_PURCHASE":
            for item in getattr(record, "items", []) or []:
                if item.product_id is not None:
                    touched_product_ids.add(item.product_id)

        await self._repo.insert_event(
            business_id=self._ctx.business_id,
            device_id=device_id,
            client_id=event.client_id,
            event_type=event.event_type,
            payload=event.payload,
            client_created_at=event.client_created_at,
            status="ACCEPTED",
            server_record_id=server_record_id,
        )
        return {
            "server_record_id": server_record_id,
            "touched_product_ids": touched_product_ids,
        }
