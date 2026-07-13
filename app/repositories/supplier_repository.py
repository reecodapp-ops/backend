"""Supplier repository — all DB access for the ``suppliers`` table."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier import Supplier


class SupplierRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self, business_id: uuid.UUID, supplier_id: uuid.UUID
    ) -> Supplier | None:
        result = await self._session.execute(
            select(Supplier).where(
                Supplier.id == supplier_id,
                Supplier.business_id == business_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        business_id: uuid.UUID,
        name: str,
        phone_number: str | None,
        notes: str | None,
        credit_terms_days: int | None = None,
        payment_flexibility: str | None = None,
    ) -> Supplier:
        supplier = Supplier(
            business_id=business_id,
            name=name,
            phone_number=phone_number,
            notes=notes,
            credit_terms_days=credit_terms_days,
            payment_flexibility=payment_flexibility,
            status="ACTIVE",
        )
        self._session.add(supplier)
        await self._session.flush()
        await self._session.refresh(supplier)
        return supplier

    async def search(
        self,
        *,
        business_id: uuid.UUID,
        search: str | None,
        status: str | None,
        order: str = "name_asc",
        limit: int,
        offset: int,
    ) -> tuple[list[Supplier], int]:
        conditions = self._conditions(business_id=business_id, search=search, status=status)

        count_stmt = select(func.count()).select_from(Supplier).where(*conditions)
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = select(Supplier).where(*conditions)
        if order == "recent":
            stmt = stmt.order_by(Supplier.created_at.desc())
        else:  # name_asc
            stmt = stmt.order_by(Supplier.name.asc())
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows), total

    async def search_all(
        self,
        *,
        business_id: uuid.UUID,
        search: str | None,
        status: str | None,
    ) -> list[Supplier]:
        """Every matching supplier, unpaginated -- used only when the caller
        needs to filter/sort by a computed (not stored) score, e.g.
        ``reliability_level`` or ``order=reliability_desc``/``terms_desc``.
        The candidate set for a single shop's supplier list is small (tens
        to low hundreds of rows), so filtering/sorting/paginating that set in
        Python after one batched score computation is the right tradeoff
        here -- see ScoringService.reliability_badges_bulk.
        """
        conditions = self._conditions(business_id=business_id, search=search, status=status)
        stmt = select(Supplier).where(*conditions).order_by(Supplier.name.asc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)

    @staticmethod
    def _conditions(
        *, business_id: uuid.UUID, search: str | None, status: str | None
    ) -> list:
        conditions = [Supplier.business_id == business_id]
        if status:
            conditions.append(Supplier.status == status)
        if search:
            conditions.append(Supplier.name.ilike(f"%{search}%"))
        return conditions

    async def flush(self) -> None:
        await self._session.flush()
