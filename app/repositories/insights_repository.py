"""Insights repository.

Reuses the *existing* efficient reads wherever one already exists:
  - best-selling products         -> DashboardRepository.get_margins_30d
                                      (``v_product_margin_30d``)
  - slow-moving / low-stock       -> DashboardRepository.get_stock_attention
                                      (``v_stock_attention``)
  - overdue debt                  -> DashboardRepository.get_debt_priority
                                      (``v_debt_priority``)
  - high-risk customers           -> CustomerRepository.search(credit_level=...)
                                      (already indexed via idx_customers_credit)

This repository only adds the handful of reads that don't already exist
anywhere: day-bounded sums for the sales/expense trend and cash-flow
comparisons, and the two anomaly checks behind "unusual activity". Every
query here is a single aggregate ``SELECT`` (no N+1, no Python-side loops
over rows) and is portable across SQLite and PostgreSQL — unlike the
view-backed reads above, which are PostgreSQL-only.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import Expense
from app.models.sale import Sale

_ZERO = Decimal("0")


class InsightsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sales_total_for_date(self, business_id: uuid.UUID, day: date) -> Decimal:
        stmt = select(func.coalesce(func.sum(Sale.total_amount), 0)).where(
            Sale.business_id == business_id,
            Sale.is_correction.is_(False),
            func.date(Sale.occurred_at) == day,
        )
        return (await self._session.execute(stmt)).scalar_one() or _ZERO

    async def expenses_total_for_date(self, business_id: uuid.UUID, day: date) -> Decimal:
        stmt = select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.business_id == business_id,
            Expense.is_correction.is_(False),
            func.date(Expense.occurred_at) == day,
        )
        return (await self._session.execute(stmt)).scalar_one() or _ZERO

    async def average_daily_expenses(
        self, business_id: uuid.UUID, *, days: int, exclude: date
    ) -> Decimal:
        """Average total expenses per calendar day over the trailing window,
        excluding ``exclude`` (normally today, so "today vs its own average"
        comparisons aren't comparing a partial day against itself).
        """

        cutoff = exclude - timedelta(days=days)
        stmt = select(
            func.coalesce(func.sum(Expense.amount), 0),
            func.count(func.distinct(func.date(Expense.occurred_at))),
        ).where(
            Expense.business_id == business_id,
            Expense.is_correction.is_(False),
            func.date(Expense.occurred_at) >= cutoff,
            func.date(Expense.occurred_at) < exclude,
        )
        total, distinct_days = (await self._session.execute(stmt)).one()
        total = total or _ZERO
        if not distinct_days:
            return _ZERO
        return total / Decimal(distinct_days)

    async def average_sale_amount(
        self, business_id: uuid.UUID, *, days: int, exclude: date
    ) -> Decimal:
        """Average individual sale amount over the trailing window (used to
        flag an unusually large single sale today)."""

        cutoff = exclude - timedelta(days=days)
        stmt = select(func.avg(Sale.total_amount)).where(
            Sale.business_id == business_id,
            Sale.is_correction.is_(False),
            func.date(Sale.occurred_at) >= cutoff,
            func.date(Sale.occurred_at) < exclude,
        )
        avg = (await self._session.execute(stmt)).scalar_one()
        return Decimal(str(avg)) if avg is not None else _ZERO

    async def max_sale_today(self, business_id: uuid.UUID, day: date) -> Decimal:
        stmt = select(func.coalesce(func.max(Sale.total_amount), 0)).where(
            Sale.business_id == business_id,
            Sale.is_correction.is_(False),
            func.date(Sale.occurred_at) == day,
        )
        return (await self._session.execute(stmt)).scalar_one() or _ZERO

    async def payments_total_for_date(self, business_id: uuid.UUID, day: date) -> Decimal:
        from app.models.payment import CustomerPayment

        stmt = select(func.coalesce(func.sum(CustomerPayment.amount), 0)).where(
            CustomerPayment.business_id == business_id,
            CustomerPayment.is_correction.is_(False),
            func.date(CustomerPayment.occurred_at) == day,
        )
        return (await self._session.execute(stmt)).scalar_one() or _ZERO

    async def stock_purchases_total_for_date(self, business_id: uuid.UUID, day: date) -> Decimal:
        from app.models.stock_purchase import StockPurchase

        stmt = select(func.coalesce(func.sum(StockPurchase.total_buying_cost), 0)).where(
            StockPurchase.business_id == business_id,
            StockPurchase.is_correction.is_(False),
            func.date(StockPurchase.occurred_at) == day,
        )
        return (await self._session.execute(stmt)).scalar_one() or _ZERO
