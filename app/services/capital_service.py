"""Capital Transaction service.

TRIGGER-BASED ARCHITECTURE (Strategy A)
---------------------------------------
The service inserts ONLY the capital_transactions row. The trigger
fn_after_capital_txn_insert looks up the direction from
capital_transaction_types and applies cash +/- accordingly.

Service responsibilities:
  1. Resolve type_code -> type_id and direction.
  2. Refuse OPENING_BALANCE here — it is only valid during business onboarding,
     and creating one post-onboarding would double-count with the
     businesses.opening_balance column (see business_service.py).
  3. Validate source_type_id only when it is supplied; clear it for non-MONEY_IN
     types since it has no meaning for outflows.
  4. For LISTING/filtering only: synthesize a single read-only OPENING_BALANCE
     "transaction" from businesses.opening_balance / opening_date, since no
     real row is ever inserted for it (see point 2). This lets the
     Money In / Money Out / Opening Balance filter behave the way an owner
     expects — opening balance shows up in the history — without violating
     the no-double-count invariant that the schema was designed around.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timezone

from app.core.business_context import BusinessContext
from app.core.exceptions import NotFoundError, ValidationError
from app.repositories.capital_repository import CapitalRepository
from app.schemas.capital import (
    CapitalSourceTypeOut,
    CapitalTransactionCreateRequest,
    CapitalTransactionCreateResponse,
    CapitalTransactionListOut,
    CapitalTransactionOut,
)

# Codes allowed via this endpoint. OPENING_BALANCE is deliberately excluded.
_ALLOWED_TYPE_CODES = {"MONEY_IN", "MONEY_OUT", "PERSONAL_USE"}
_OPENING_BALANCE_NAMESPACE = uuid.UUID("6f6f2e0a-2b1a-4e2e-9a3c-8b8f8e6a0b10")


class CapitalService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = CapitalRepository(ctx.session)

    async def create_capital_transaction(
        self, data: CapitalTransactionCreateRequest
    ) -> CapitalTransactionCreateResponse:
        biz_id = self._ctx.business_id

        if data.type_code not in _ALLOWED_TYPE_CODES:
            raise ValidationError(
                "OPENING_BALANCE is only recorded during business onboarding.",
                code="opening_balance_not_allowed",
            )

        capital_type = await self._repo.get_type_by_code(data.type_code)
        if capital_type is None:
            raise ValidationError(
                "Unknown capital transaction type.", code="invalid_capital_type"
            )

        # source_type_id is meaningful only for MONEY_IN.
        effective_source_id: int | None = None
        if data.type_code == "MONEY_IN" and data.source_type_id is not None:
            source = await self._repo.get_source_type(data.source_type_id)
            if source is None:
                raise ValidationError(
                    "Unknown capital source type.", code="invalid_capital_source"
                )
            effective_source_id = source.id

        occurred_at = data.occurred_at or datetime.now(timezone.utc)

        txn = await self._repo.insert_capital_transaction(
            business_id=biz_id,
            type_id=capital_type.id,
            source_type_id=effective_source_id,
            amount=data.amount,
            occurred_at=occurred_at,
            note=data.note,
        )

        await self._ctx.session.flush()
        await self._ctx.session.refresh(txn)

        return CapitalTransactionCreateResponse(
            capital_transaction=CapitalTransactionOut(
                id=txn.id,
                business_id=txn.business_id,
                type_id=txn.type_id,
                type_code=capital_type.code,
                direction=capital_type.direction,
                source_type_id=txn.source_type_id,
                amount=txn.amount,
                occurred_at=txn.occurred_at,
                note=txn.note,
                is_correction=txn.is_correction,
                created_at=txn.created_at,
            )
        )

    async def get_capital_transaction(
        self, txn_id: uuid.UUID
    ) -> CapitalTransactionOut:
        if txn_id == self._opening_balance_id():
            entry = self._opening_balance_entry()
            if entry is None:
                raise NotFoundError(
                    "Capital transaction not found.", code="capital_transaction_not_found"
                )
            return entry
        result = await self._repo.get_transaction_with_type(
            self._ctx.business_id, txn_id
        )
        if result is None:
            raise NotFoundError(
                "Capital transaction not found.", code="capital_transaction_not_found"
            )
        t, tt = result
        return CapitalTransactionOut(
            id=t.id,
            business_id=t.business_id,
            type_id=t.type_id,
            type_code=tt.code,
            direction=tt.direction,
            source_type_id=t.source_type_id,
            amount=t.amount,
            occurred_at=t.occurred_at,
            note=t.note,
            is_correction=t.is_correction,
            created_at=t.created_at,
        )

    async def list_capital_transactions(
        self,
        *,
        type_code: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
        offset: int,
    ) -> CapitalTransactionListOut:
        # OPENING_BALANCE never has a real row (see module docstring); serve
        # it entirely from the synthetic entry when specifically requested.
        if type_code == "OPENING_BALANCE":
            entry = self._opening_balance_entry(date_from=date_from, date_to=date_to)
            items = [entry] if entry is not None else []
            return CapitalTransactionListOut(
                items=items[offset : offset + limit],
                total=len(items),
                limit=limit,
                offset=offset,
            )

        rows, total = await self._repo.list_capital_transactions(
            business_id=self._ctx.business_id,
            type_code=type_code,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        items = [
            CapitalTransactionOut(
                id=t.id,
                business_id=t.business_id,
                type_id=t.type_id,
                type_code=tt.code,
                direction=tt.direction,
                source_type_id=t.source_type_id,
                amount=t.amount,
                occurred_at=t.occurred_at,
                note=t.note,
                is_correction=t.is_correction,
                created_at=t.created_at,
            )
            for t, tt in rows
        ]

        # No type filter: splice the opening balance in (once) so an
        # unfiltered history view matches what the owner actually did.
        total_with_opening = total
        if type_code is None:
            entry = self._opening_balance_entry(date_from=date_from, date_to=date_to)
            if entry is not None:
                items = items + [entry]

                def _sort_key(i: CapitalTransactionOut) -> datetime:
                    occurred = i.occurred_at
                    if occurred.tzinfo is None:
                        occurred = occurred.replace(tzinfo=timezone.utc)
                    return occurred

                items.sort(key=_sort_key, reverse=True)
                total_with_opening += 1
                items = items[:limit]

        return CapitalTransactionListOut(
            items=items, total=total_with_opening, limit=limit, offset=offset
        )

    async def list_source_types(self) -> list[CapitalSourceTypeOut]:
        rows = await self._repo.list_source_types()
        return [CapitalSourceTypeOut.model_validate(r) for r in rows]

    # ----- opening balance synthesis ----------------------------------------- #
    def _opening_balance_id(self) -> uuid.UUID:
        return uuid.uuid5(_OPENING_BALANCE_NAMESPACE, str(self._ctx.business_id))

    def _opening_balance_entry(
        self,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> CapitalTransactionOut | None:
        business = self._ctx.business
        if business.opening_balance is None or business.opening_balance <= 0:
            return None
        occurred_at = datetime.combine(
            business.opening_date, time.min, tzinfo=timezone.utc
        )
        if date_from is not None and occurred_at < date_from:
            return None
        if date_to is not None and occurred_at > date_to:
            return None
        return CapitalTransactionOut(
            id=self._opening_balance_id(),
            business_id=business.id,
            type_id=0,
            type_code="OPENING_BALANCE",
            direction="I",
            source_type_id=None,
            amount=business.opening_balance,
            occurred_at=occurred_at,
            note="Opening balance at business setup",
            is_correction=False,
            created_at=occurred_at,
        )
