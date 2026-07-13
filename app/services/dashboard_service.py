"""Dashboard service.

Mostly a pass-through over the database views (v_daily_summary /
v_debt_priority / v_debt_aging / v_stock_attention / v_product_margin_30d /
v_expense_pressure_30d) — no analytics are recomputed here for those.

The two exceptions are documented where they're implemented:
  * ``get_today`` merges in stock_purchases_today / profit_today, computed
    directly from source tables (the view doesn't carry them).
  * ``get_activity`` is a portable (SQLite + PostgreSQL) UNION ALL across the
    five transaction tables rather than a read of the PostgreSQL-only
    ``v_today_activity`` view, so it can support today/yesterday/custom date
    ranges with real pagination. See DashboardRepository.get_activity.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.business_context import BusinessContext
from app.core.exceptions import NotFoundError, ValidationError
from app.repositories.dashboard_repository import DashboardRepository
from app.schemas.dashboard import (
    ActivityListOut,
    ActivityRow,
    DailySummaryOut,
    DebtAgingListOut,
    DebtAgingRow,
    DebtPriorityListOut,
    DebtPriorityRow,
    ExpensePressureListOut,
    ExpensePressureRow,
    ProductMarginListOut,
    ProductMarginRow,
    StockAttentionListOut,
    StockAttentionRow,
)


class DashboardService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = DashboardRepository(ctx.session)

    async def get_today(self) -> DailySummaryOut:
        row = await self._repo.get_daily_summary(self._ctx.business_id)
        if row is None:
            # If for some reason no row exists in the view (e.g. business was
            # deleted between dependency resolution and query), surface 404.
            raise NotFoundError(
                "Daily summary not available for this business.",
                code="daily_summary_unavailable",
            )
        extras = await self._repo.get_today_extras(self._ctx.business_id)
        return DailySummaryOut.model_validate({**dict(row), **extras})

    async def get_debt_priority(self) -> DebtPriorityListOut:
        rows = await self._repo.get_debt_priority(self._ctx.business_id)
        return DebtPriorityListOut(
            items=[DebtPriorityRow.model_validate(r) for r in rows]
        )

    async def get_debt_aging(self) -> DebtAgingListOut:
        rows = await self._repo.get_debt_aging(self._ctx.business_id)
        return DebtAgingListOut(items=[DebtAgingRow.model_validate(r) for r in rows])

    async def get_stock_attention(self, status: str | None) -> StockAttentionListOut:
        rows = await self._repo.get_stock_attention(self._ctx.business_id, status)
        return StockAttentionListOut(
            items=[StockAttentionRow.model_validate(r) for r in rows]
        )

    async def get_margins(self) -> ProductMarginListOut:
        rows = await self._repo.get_margins_30d(self._ctx.business_id)
        return ProductMarginListOut(
            items=[ProductMarginRow.model_validate(r) for r in rows]
        )

    async def get_expense_pressure(self) -> ExpensePressureListOut:
        rows = await self._repo.get_expense_pressure_30d(self._ctx.business_id)
        return ExpensePressureListOut(
            items=[ExpensePressureRow.model_validate(r) for r in rows]
        )

    async def get_activity(
        self,
        *,
        period: str = "custom",
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ActivityListOut:
        """Unified feed across sales, payments, stock purchases, expenses,
        and capital transactions. ``period`` is a convenience over explicit
        dates:
          - "today":     [start of today UTC, now]
          - "yesterday": [start of yesterday UTC, start of today UTC)
          - "custom":    uses date_from/date_to as given (either may be None
                          for an open-ended bound)
        """
        if period == "today":
            start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            date_from, date_to = start, datetime.now(timezone.utc)
        elif period == "yesterday":
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            date_from = today_start - timedelta(days=1)
            date_to = today_start
        elif period != "custom":
            raise ValidationError(
                "period must be one of: today, yesterday, custom.",
                code="invalid_activity_period",
            )

        rows, total = await self._repo.get_activity(
            self._ctx.business_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        return ActivityListOut(
            items=[ActivityRow.model_validate(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
