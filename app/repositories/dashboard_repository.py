"""Dashboard repository.

Reads strictly from the database views defined in dukamate_schema_v3.sql:
  - v_daily_summary
  - v_debt_priority
  - v_debt_aging
  - v_stock_attention
  - v_product_margin_30d
  - v_expense_pressure_30d

No analytics are computed in Python; the views own all aggregation, bucketing,
priority scoring, and aging logic. RLS scopes results to the calling business
via app.current_business_id on PostgreSQL; on SQLite (no RLS) we add an explicit
WHERE business_id = :biz filter in every query for the same effect.

THE UNIFIED ACTIVITY FEED IS THE ONE EXCEPTION
------------------------------------------------
``v_today_activity`` (schema NEW v3) is a plain, undated UNION ALL of every
transaction table; it has no date filtering or pagination of its own — the
name is a UI convention, not something the view enforces. Rather than filter
that PostgreSQL-only view with a raw ``date_trunc`` predicate (as an earlier
version of this method did, which made the whole activity feed unusable in
SQLite tests), ``get_activity`` below builds the equivalent UNION ALL directly
with SQLAlchemy Core across the five source tables. This is 100% portable
(SQLite in tests, PostgreSQL in production) and supports a real date range +
LIMIT/OFFSET pushed down to the database, which the view alone cannot give us.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, literal, select, text, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import CapitalTransaction
from app.models.expense import Expense
from app.models.payment import CustomerPayment
from app.models.sale import Sale
from app.models.stock_purchase import StockPurchase


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _biz_param(self, business_id: uuid.UUID) -> str:
        """Format the UUID for a text() bind matching how the dialect stores it.

        SQLAlchemy stores PG_UUID values as 32-char hex strings on SQLite (no
        hyphens), but as standard hyphenated UUIDs on PostgreSQL. A raw text()
        comparison needs the right format for each."""
        bind = self._session.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            return business_id.hex
        return str(business_id)

    async def get_daily_summary(self, business_id: uuid.UUID) -> dict | None:
        result = await self._session.execute(
            text("SELECT * FROM v_daily_summary WHERE business_id = :biz"),
            {"biz": self._biz_param(business_id)},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_debt_priority(self, business_id: uuid.UUID) -> list[dict]:
        result = await self._session.execute(
            text(
                "SELECT * FROM v_debt_priority "
                "WHERE business_id = :biz "
                "ORDER BY priority_score DESC"
            ),
            {"biz": self._biz_param(business_id)},
        )
        return [dict(r) for r in result.mappings().all()]

    async def get_debt_aging(self, business_id: uuid.UUID) -> list[dict]:
        result = await self._session.execute(
            text(
                "SELECT * FROM v_debt_aging "
                "WHERE business_id = :biz "
                "ORDER BY incurred_at ASC"
            ),
            {"biz": self._biz_param(business_id)},
        )
        return [dict(r) for r in result.mappings().all()]

    async def get_stock_attention(
        self, business_id: uuid.UUID, status: str | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM v_stock_attention WHERE business_id = :biz"
        params: dict = {"biz": self._biz_param(business_id)}
        if status is not None:
            sql += " AND stock_status = :st"
            params["st"] = status
        result = await self._session.execute(text(sql), params)
        return [dict(r) for r in result.mappings().all()]

    async def get_margins_30d(self, business_id: uuid.UUID) -> list[dict]:
        result = await self._session.execute(
            text(
                "SELECT * FROM v_product_margin_30d "
                "WHERE business_id = :biz "
                "ORDER BY gross_margin DESC"
            ),
            {"biz": self._biz_param(business_id)},
        )
        return [dict(r) for r in result.mappings().all()]

    async def get_expense_pressure_30d(self, business_id: uuid.UUID) -> list[dict]:
        result = await self._session.execute(
            text(
                "SELECT * FROM v_expense_pressure_30d "
                "WHERE business_id = :biz "
                "ORDER BY total_spent DESC"
            ),
            {"biz": self._biz_param(business_id)},
        )
        return [dict(r) for r in result.mappings().all()]

    async def get_today_extras(self, business_id: uuid.UUID) -> dict:
        """Metrics the schema's ``v_daily_summary`` doesn't carry: today's
        stock purchases total and today's gross profit. Computed directly
        from ``stock_purchases`` and ``sales.gross_margin`` (a GENERATED
        column) so no new view/migration is needed — see MIGRATION_NOTES.md.
        Portable across SQLite/PostgreSQL (uses ``func.date`` for the day
        boundary rather than a PostgreSQL-only date function).
        """
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).date()

        purchases_stmt = select(func.coalesce(func.sum(StockPurchase.total_buying_cost), 0)).where(
            StockPurchase.business_id == business_id,
            StockPurchase.is_correction.is_(False),
            func.date(StockPurchase.occurred_at) == today,
        )
        stock_purchases_today = (await self._session.execute(purchases_stmt)).scalar_one()

        profit_stmt = select(
            func.coalesce(func.sum(Sale.total_amount - Sale.total_buying_cost), 0)
        ).where(
            Sale.business_id == business_id,
            Sale.is_correction.is_(False),
            func.date(Sale.occurred_at) == today,
        )
        profit_today = (await self._session.execute(profit_stmt)).scalar_one()

        return {
            "stock_purchases_today": stock_purchases_today,
            "profit_today": profit_today,
        }

    # ----- unified activity feed [portable — see module docstring] ----------- #
    async def get_activity(
        self,
        business_id: uuid.UUID,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        """Sales + customer payments + stock purchases + expenses + capital
        transactions, merged into one feed, newest first, with a real
        (database-level) date-range filter and pagination.
        """

        def _branch(model, activity_type: str, amount_col):
            conditions = [model.business_id == business_id]
            if hasattr(model, "is_correction"):
                conditions.append(model.is_correction.is_(False))
            if date_from is not None:
                conditions.append(model.occurred_at >= date_from)
            if date_to is not None:
                conditions.append(model.occurred_at <= date_to)
            return select(
                model.business_id.label("business_id"),
                literal(activity_type).label("activity_type"),
                model.id.label("record_id"),
                amount_col.label("amount"),
                model.occurred_at.label("occurred_at"),
            ).where(*conditions)

        branches = [
            _branch(Sale, "SALE", Sale.total_amount),
            _branch(Expense, "EXPENSE", Expense.amount),
            _branch(StockPurchase, "STOCK_PURCHASE", StockPurchase.total_buying_cost),
            _branch(CapitalTransaction, "CAPITAL_TRANSACTION", CapitalTransaction.amount),
            _branch(CustomerPayment, "CUSTOMER_PAYMENT", CustomerPayment.amount),
        ]
        activity = union_all(*branches).subquery("activity")

        count_stmt = select(func.count()).select_from(activity)
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = (
            select(activity)
            .order_by(activity.c.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        return [dict(r) for r in rows], total
