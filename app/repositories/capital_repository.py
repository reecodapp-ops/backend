"""Capital transaction repository.

Strategy A: inserts only the capital_transactions row; the trigger applies cash
direction based on capital_transaction_types.direction.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import CapitalTransaction
from app.models.lookups import CapitalSourceType, CapitalTransactionType


class CapitalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----- lookups ---------------------------------------------------------- #
    async def get_type_by_code(self, code: str) -> CapitalTransactionType | None:
        result = await self._session.execute(
            select(CapitalTransactionType).where(
                CapitalTransactionType.code == code
            )
        )
        return result.scalar_one_or_none()

    async def get_type_by_id(self, type_id: int) -> CapitalTransactionType | None:
        result = await self._session.execute(
            select(CapitalTransactionType).where(
                CapitalTransactionType.id == type_id
            )
        )
        return result.scalar_one_or_none()

    async def get_source_type(self, source_type_id: int) -> CapitalSourceType | None:
        result = await self._session.execute(
            select(CapitalSourceType).where(CapitalSourceType.id == source_type_id)
        )
        return result.scalar_one_or_none()

    async def list_source_types(self) -> list[CapitalSourceType]:
        result = await self._session.execute(
            select(CapitalSourceType).order_by(CapitalSourceType.sort_order.asc())
        )
        return list(result.scalars().all())

    # ----- write ----------------------------------------------------------- #
    async def insert_capital_transaction(
        self,
        *,
        business_id: uuid.UUID,
        type_id: int,
        source_type_id: int | None,
        amount: Decimal,
        occurred_at: datetime,
        note: str | None,
    ) -> CapitalTransaction:
        txn = CapitalTransaction(
            business_id=business_id,
            type_id=type_id,
            source_type_id=source_type_id,
            amount=amount,
            occurred_at=occurred_at,
            note=note,
            is_correction=False,
        )
        self._session.add(txn)
        await self._session.flush()
        return txn

    # ----- read ----------------------------------------------------------- #
    async def list_capital_transactions(
        self,
        *,
        business_id: uuid.UUID,
        type_code: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[CapitalTransaction, CapitalTransactionType]], int]:
        """Return capital txns joined to their type rows so the response can
        include the code and direction without N+1 lookups."""
        conditions = [CapitalTransaction.business_id == business_id]
        if date_from is not None:
            conditions.append(CapitalTransaction.occurred_at >= date_from)
        if date_to is not None:
            conditions.append(CapitalTransaction.occurred_at <= date_to)
        if type_code is not None:
            conditions.append(CapitalTransactionType.code == type_code)

        base = (
            select(CapitalTransaction, CapitalTransactionType)
            .join(
                CapitalTransactionType,
                CapitalTransactionType.id == CapitalTransaction.type_id,
            )
            .where(*conditions)
        )
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = (
            base.order_by(CapitalTransaction.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(t, tt) for t, tt in rows], total

    async def get_transaction_with_type(
        self, business_id: uuid.UUID, txn_id: uuid.UUID
    ) -> tuple[CapitalTransaction, CapitalTransactionType] | None:
        result = await self._session.execute(
            select(CapitalTransaction, CapitalTransactionType)
            .join(
                CapitalTransactionType,
                CapitalTransactionType.id == CapitalTransaction.type_id,
            )
            .where(
                CapitalTransaction.id == txn_id,
                CapitalTransaction.business_id == business_id,
            )
        )
        row = result.first()
        if row is None:
            return None
        return row[0], row[1]

    async def flush(self) -> None:
        await self._session.flush()
