"""Expense repository.

Strategy A: inserts only the expenses row; the trigger reduces
businesses.cash_on_hand. Also exposes the category validator and history listing.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import Expense, ExpenseCategory


class ExpenseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----- category lookups ------------------------------------------------- #
    async def get_visible_category(
        self, business_id: uuid.UUID, category_id: int
    ) -> ExpenseCategory | None:
        """A category the business can use: system default (business_id NULL) or its own."""
        result = await self._session.execute(
            select(ExpenseCategory).where(
                ExpenseCategory.id == category_id,
                or_(
                    ExpenseCategory.business_id == business_id,
                    ExpenseCategory.business_id.is_(None),
                ),
                ExpenseCategory.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def list_visible_categories(
        self, business_id: uuid.UUID
    ) -> list[ExpenseCategory]:
        stmt = (
            select(ExpenseCategory)
            .where(
                or_(
                    ExpenseCategory.business_id == business_id,
                    ExpenseCategory.business_id.is_(None),
                ),
                ExpenseCategory.is_active.is_(True),
            )
            .order_by(ExpenseCategory.sort_order.asc(), ExpenseCategory.label.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)

    # ----- write ----------------------------------------------------------- #
    async def insert_expense(
        self,
        *,
        business_id: uuid.UUID,
        category_id: int,
        amount: Decimal,
        occurred_at: datetime,
        note: str | None,
        supplier_name: str | None,
    ) -> Expense:
        expense = Expense(
            business_id=business_id,
            category_id=category_id,
            amount=amount,
            occurred_at=occurred_at,
            note=note,
            supplier_name=supplier_name,
            is_correction=False,
        )
        self._session.add(expense)
        await self._session.flush()
        return expense

    # ----- read / history -------------------------------------------------- #
    async def get_by_id(
        self, business_id: uuid.UUID, expense_id: uuid.UUID
    ) -> Expense | None:
        result = await self._session.execute(
            select(Expense).where(
                Expense.id == expense_id, Expense.business_id == business_id
            )
        )
        return result.scalar_one_or_none()

    async def list_expenses(
        self,
        *,
        business_id: uuid.UUID,
        category_id: int | None,
        group_id: int | None,
        search: str | None = None,
        date_from: datetime | None,
        date_to: datetime | None,
        min_amount: Decimal | None = None,
        max_amount: Decimal | None = None,
        order: str = "recent",
        limit: int,
        offset: int,
    ) -> tuple[list[Expense], int]:
        conditions = [Expense.business_id == business_id]
        if category_id is not None:
            conditions.append(Expense.category_id == category_id)
        if group_id is not None:
            # filter by category's group_id via join
            subq = select(ExpenseCategory.id).where(
                ExpenseCategory.group_id == group_id
            )
            conditions.append(Expense.category_id.in_(subq))
        if search:
            conditions.append(
                or_(
                    Expense.note.ilike(f"%{search}%"),
                    Expense.supplier_name.ilike(f"%{search}%"),
                )
            )
        if date_from is not None:
            conditions.append(Expense.occurred_at >= date_from)
        if date_to is not None:
            conditions.append(Expense.occurred_at <= date_to)
        if min_amount is not None:
            conditions.append(Expense.amount >= min_amount)
        if max_amount is not None:
            conditions.append(Expense.amount <= max_amount)

        count_stmt = select(func.count()).select_from(Expense).where(*conditions)
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = select(Expense).where(*conditions)
        if order == "amount_desc":
            stmt = stmt.order_by(Expense.amount.desc())
        elif order == "amount_asc":
            stmt = stmt.order_by(Expense.amount.asc())
        elif order == "oldest":
            stmt = stmt.order_by(Expense.occurred_at.asc())
        else:  # recent
            stmt = stmt.order_by(Expense.occurred_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows), total

    async def recent_average_for_category(
        self, business_id: uuid.UUID, category_id: int, days: int = 90
    ) -> Decimal | None:
        """Average expense amount for a category over the last N days, used to
        detect 'unusual amount' soft warnings."""
        cutoff_stmt = select(func.now())  # use server NOW(); SQLite handles it too
        # Pull recent values then compute average in Python (cross-dialect safe).
        stmt = select(Expense.amount, Expense.occurred_at).where(
            Expense.business_id == business_id,
            Expense.category_id == category_id,
            Expense.is_correction.is_(False),
        )
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return None
        from datetime import timedelta, timezone

        now_row = (await self._session.execute(cutoff_stmt)).scalar_one()
        # normalise to aware datetime for comparison
        if now_row.tzinfo is None:
            now_row = now_row.replace(tzinfo=timezone.utc)
        cutoff = now_row - timedelta(days=days)
        recent = []
        for amt, occ in rows:
            o = occ
            if o is not None and o.tzinfo is None:
                o = o.replace(tzinfo=timezone.utc)
            if o is None or o >= cutoff:
                recent.append(amt)
        if not recent:
            return None
        return sum(recent, Decimal("0")) / Decimal(len(recent))

    async def flush(self) -> None:
        await self._session.flush()
