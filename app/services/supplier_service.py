"""Supplier service — create, update, search (and archive via update.status)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.business_context import BusinessContext
from app.core.exceptions import NotFoundError
from app.core.phone import resolve_phone_number
from app.models.supplier import Supplier
from app.repositories.supplier_repository import SupplierRepository
from app.schemas.scoring import ScoreOut
from app.schemas.supplier import (
    SupplierCreateRequest,
    SupplierListOut,
    SupplierOut,
    SupplierUpdateRequest,
)
from app.services.scoring_service import ScoringService

# Sort orders that require a computed (not stored) score -- these force the
# "fetch all matching candidates, score once in bulk, filter/sort/paginate in
# Python" path instead of DB-level LIMIT/OFFSET. See ScoringService's bulk
# methods for why this stays a fixed, small number of queries regardless of
# how many suppliers are involved.
_SCORE_BASED_ORDERS = {"reliability_desc", "terms_desc"}


def _sort_key(score: ScoreOut) -> int:
    """Sort key for a descending score-based order: insufficient_data sorts
    last (as if -1), never crashes on a None percentage."""
    return score.percentage if score.percentage is not None else -1


class SupplierService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = SupplierRepository(ctx.session)

    async def create(self, data: SupplierCreateRequest) -> SupplierOut:
        phone_number = await resolve_phone_number(
            self._ctx.session,
            phone_country_id=data.phone_country_id,
            phone_local_number=data.phone_local_number,
            legacy_phone_number=data.phone_number,
        )
        supplier = await self._repo.create(
            business_id=self._ctx.business_id,
            name=data.name,
            phone_number=phone_number,
            notes=data.notes,
            credit_terms_days=data.credit_terms_days,
            payment_flexibility=data.payment_flexibility,
        )
        return SupplierOut.model_validate(supplier)

    async def update(
        self, supplier_id: uuid.UUID, data: SupplierUpdateRequest
    ) -> SupplierOut:
        supplier = await self._get_or_404(supplier_id)
        if data.name is not None:
            supplier.name = data.name
        if data.phone_country_id is not None:
            supplier.phone_number = await resolve_phone_number(
                self._ctx.session,
                phone_country_id=data.phone_country_id,
                phone_local_number=data.phone_local_number,
                legacy_phone_number=data.phone_number,
            )
        elif data.phone_number is not None:
            supplier.phone_number = data.phone_number
        if data.notes is not None:
            supplier.notes = data.notes
        if data.credit_terms_days is not None:
            supplier.credit_terms_days = data.credit_terms_days
        if data.payment_flexibility is not None:
            supplier.payment_flexibility = data.payment_flexibility
        if data.status is not None:
            supplier.status = data.status
        supplier.updated_at = datetime.now(timezone.utc)
        await self._repo.flush()
        await self._ctx.session.refresh(supplier)
        return SupplierOut.model_validate(supplier)

    async def archive(self, supplier_id: uuid.UUID) -> SupplierOut:
        """Soft-delete: set status = ARCHIVED. Purchase history is preserved."""
        supplier = await self._get_or_404(supplier_id)
        supplier.status = "ARCHIVED"
        supplier.updated_at = datetime.now(timezone.utc)
        await self._repo.flush()
        await self._ctx.session.refresh(supplier)
        return SupplierOut.model_validate(supplier)

    async def get(self, supplier_id: uuid.UUID) -> SupplierOut:
        supplier = await self._get_or_404(supplier_id)
        return SupplierOut.model_validate(supplier)

    async def search(
        self,
        *,
        search: str | None,
        status: str | None,
        reliability_level: str | None = None,
        order: str = "name_asc",
        limit: int,
        offset: int,
    ) -> SupplierListOut:
        scoring = ScoringService(self._ctx)
        business_id = self._ctx.business_id

        if reliability_level is None and order not in _SCORE_BASED_ORDERS:
            # Common path: normal DB-level pagination, then one batched
            # reliability computation for just this page's rows.
            rows, total = await self._repo.search(
                business_id=business_id,
                search=search,
                status=status,
                order=order,
                limit=limit,
                offset=offset,
            )
            badges = await scoring.reliability_badges_bulk([r.id for r in rows])
            return SupplierListOut(
                items=[self._to_out(r, badges.get(r.id)) for r in rows],
                total=total,
                limit=limit,
                offset=offset,
            )

        # reliability_level filter and/or a score-based sort: neither can be
        # expressed as a SQL WHERE/ORDER BY against a stored column (these
        # scores are computed on read, not materialized). Fetch every
        # matching candidate once, score the whole set in one batched pass,
        # then filter/sort/paginate in Python. Still exactly one (or two,
        # if sorting by terms) bulk scoring round trip -- never one per row.
        all_rows = await self._repo.search_all(
            business_id=business_id, search=search, status=status
        )
        all_ids = [r.id for r in all_rows]
        reliability_badges = await scoring.reliability_badges_bulk(all_ids)

        if reliability_level is not None:
            all_rows = [
                r for r in all_rows
                if scoring.reliability_level_key(reliability_badges[r.id]) == reliability_level
            ]

        if order == "terms_desc":
            terms_scores = await scoring.terms_scores_bulk(
                business_id, [r.id for r in all_rows]
            )
            all_rows.sort(
                key=lambda r: _sort_key(terms_scores[r.id]), reverse=True
            )
        elif order == "reliability_desc":
            all_rows.sort(
                key=lambda r: _sort_key(reliability_badges[r.id]), reverse=True
            )

        total = len(all_rows)
        page = all_rows[offset : offset + limit]
        return SupplierListOut(
            items=[self._to_out(r, reliability_badges.get(r.id)) for r in page],
            total=total,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _to_out(supplier: Supplier, reliability: ScoreOut | None) -> SupplierOut:
        out = SupplierOut.model_validate(supplier)
        if reliability is not None:
            label, color = ScoringService.reliability_badge(reliability)
            out.reliability_label = label
            out.reliability_color = color
        return out

    async def _get_or_404(self, supplier_id: uuid.UUID) -> Supplier:
        supplier = await self._repo.get_by_id(self._ctx.business_id, supplier_id)
        if supplier is None:
            raise NotFoundError("Supplier not found.", code="supplier_not_found")
        return supplier
