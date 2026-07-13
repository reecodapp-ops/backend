"""Expense service.

TRIGGER-BASED ARCHITECTURE (Strategy A)
---------------------------------------
The service inserts ONLY the expenses row. The trigger fn_after_expense_insert
subtracts the amount from businesses.cash_on_hand. The service never touches
cash directly.

Service responsibilities:
  1. Validate the category exists and is usable by this business.
  2. Compute an UNUSUAL_AMOUNT soft warning when the amount deviates substantially
     from the recent average for that category (never blocks).
  3. List/filter expense history.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.core.business_context import BusinessContext
from app.core.exceptions import NotFoundError, ValidationError
from app.repositories.expense_repository import ExpenseRepository
from app.schemas.expense import (
    ExpenseCategoryOut,
    ExpenseCreateRequest,
    ExpenseCreateResponse,
    ExpenseListOut,
    ExpenseOut,
    ExpenseWarning,
)

# An expense flagged as unusual if it is at least this multiple of the recent
# average for the same category. Conservative — avoids nagging on small swings.
_UNUSUAL_MULTIPLIER = Decimal("2.5")


class ExpenseService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = ExpenseRepository(ctx.session)

    async def create_expense(
        self, data: ExpenseCreateRequest
    ) -> ExpenseCreateResponse:
        biz_id = self._ctx.business_id

        category = await self._repo.get_visible_category(biz_id, data.category_id)
        if category is None:
            raise ValidationError(
                "Unknown or inaccessible expense category.",
                code="invalid_expense_category",
            )

        warnings: list[ExpenseWarning] = []
        avg = await self._repo.recent_average_for_category(biz_id, data.category_id)
        if avg is not None and avg > 0 and data.amount >= avg * _UNUSUAL_MULTIPLIER:
            warnings.append(
                ExpenseWarning(
                    code="UNUSUAL_AMOUNT",
                    message=(
                        f"Amount {data.amount} is well above the recent average "
                        f"of {avg:.2f} for this category."
                    ),
                )
            )

        occurred_at = data.occurred_at or datetime.now(timezone.utc)

        expense = await self._repo.insert_expense(
            business_id=biz_id,
            category_id=data.category_id,
            amount=data.amount,
            occurred_at=occurred_at,
            note=data.note,
            supplier_name=data.supplier_name,
        )

        await self._ctx.session.flush()
        await self._ctx.session.refresh(expense)

        return ExpenseCreateResponse(
            expense=ExpenseOut.model_validate(expense), warnings=warnings
        )

    async def get_expense(self, expense_id: uuid.UUID) -> ExpenseOut:
        expense = await self._repo.get_by_id(self._ctx.business_id, expense_id)
        if expense is None:
            raise NotFoundError("Expense not found.", code="expense_not_found")
        return ExpenseOut.model_validate(expense)

    async def list_expenses(
        self,
        *,
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
    ) -> ExpenseListOut:
        rows, total = await self._repo.list_expenses(
            business_id=self._ctx.business_id,
            category_id=category_id,
            group_id=group_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
            min_amount=min_amount,
            max_amount=max_amount,
            order=order,
            limit=limit,
            offset=offset,
        )
        return ExpenseListOut(
            items=[ExpenseOut.model_validate(e) for e in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def list_categories(self) -> list[ExpenseCategoryOut]:
        rows = await self._repo.list_visible_categories(self._ctx.business_id)
        return [ExpenseCategoryOut.model_validate(c) for c in rows]
