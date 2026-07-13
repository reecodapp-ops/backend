"""Business Insights service.

Turns the business's existing data — read through the same views and
repositories the rest of the app already uses — into a short, bounded list of
human-readable recommendations and warnings for the owner. No new analytics
tables or heavy computation: every insight is either a thin read of an
existing view (``v_product_margin_30d``, ``v_stock_attention``,
``v_debt_priority``) or a single portable aggregate query
(``InsightsRepository``), then formatted into a sentence.

EXTENSIBILITY
-------------
``_GENERATORS`` is a plain list of bound async methods, each returning
``list[InsightOut]`` (usually 0 or 1 items, occasionally 2). Adding a new
insight category is: write one more ``_xxx`` method following the same
signature and append it to the list — nothing else in this file, the
schema, or the router needs to change.

VIEW AVAILABILITY
------------------
``v_product_margin_30d``, ``v_stock_attention``, and ``v_debt_priority`` are
PostgreSQL-only (see dukamate_schema_v3.sql). On any other backend (e.g.
SQLite in tests) the view-backed generators are skipped rather than raising,
exactly like ``app/workers/reconciliation.py`` does for its PostgreSQL-only
function call.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.user import User
from app.repositories.customer_repository import CustomerRepository
from app.repositories.dashboard_repository import DashboardRepository
from app.repositories.insights_repository import InsightsRepository
from app.schemas.insights import InsightOut, InsightsListOut
from app.services.business_service import BusinessService

_ZERO = Decimal("0")
_SEVERITY_RANK = {"ALERT": 0, "WARN": 1, "INFO": 2}

# Thresholds — conservative defaults, matching the style of
# app/services/issue_service.py's engine constants.
_BEST_SELLING_TOP_N = 3
_SLOW_MOVER_TOP_N = 3
_LOW_STOCK_TOP_N = 5
_OVERDUE_TOP_N = 3
_HIGH_RISK_TOP_N = 5
_EXPENSE_TO_SALES_WARN_RATIO = Decimal("0.6")
_UNUSUAL_EXPENSE_MULTIPLIER = Decimal("2.5")
_UNUSUAL_SALE_MULTIPLIER = Decimal("3.0")


class InsightsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._business_service = BusinessService(session)
        self._dashboard_repo = DashboardRepository(session)
        self._customer_repo = CustomerRepository(session)
        self._insights_repo = InsightsRepository(session)
        self._GENERATORS = [
            self._best_selling_products,
            self._slow_moving_products,
            self._low_stock_products,
            self._overdue_debt,
            self._high_risk_customers,
            self._sales_trend,
            self._expense_pressure,
            self._cash_flow_summary,
            self._unusual_activity,
        ]

    async def get_insights(
        self, owner: User, business_id: uuid.UUID
    ) -> InsightsListOut:
        """GET /businesses/{business_id}/insights.

        Verifies ownership (reusing BusinessService's gate — same RLS/
        authorization path as every other business-scoped read), then runs
        every registered generator and returns the combined, severity-sorted
        list.
        """
        business = await self._business_service.get_authorized_business(
            owner, business_id
        )

        items: list[InsightOut] = []
        for generator in self._GENERATORS:
            items.extend(await generator(business))

        items.sort(key=lambda i: (_SEVERITY_RANK.get(i.severity, 3), i.category))

        return InsightsListOut(
            business_id=business_id,
            generated_at=datetime.now(timezone.utc),
            items=items,
        )

    # ----- view-backed generators [PostgreSQL only] --------------------------- #
    def _is_postgres(self) -> bool:
        bind = self._session.get_bind()
        return bind is not None and bind.dialect.name == "postgresql"

    async def _best_selling_products(self, business: Business) -> list[InsightOut]:
        if not self._is_postgres():
            return []
        rows = await self._dashboard_repo.get_margins_30d(business.id)
        # v_product_margin_30d's own default order is by gross_margin, not
        # revenue — re-sort explicitly so "best sellers by revenue" is
        # actually accurate regardless of the view's ORDER BY.
        with_sales = [r for r in rows if r.get("units_sold")]
        top = sorted(with_sales, key=lambda r: r["revenue"], reverse=True)[:_BEST_SELLING_TOP_N]
        if not top:
            return []
        names = ", ".join(f"{r['product_name']} ({r['revenue']})" for r in top)
        return [
            InsightOut(
                category="BEST_SELLING_PRODUCTS",
                severity="INFO",
                title="Your best sellers this month",
                message=f"Top performers by revenue in the last 30 days: {names}.",
                data={"products": top},
            )
        ]

    async def _slow_moving_products(self, business: Business) -> list[InsightOut]:
        if not self._is_postgres():
            return []
        rows = await self._dashboard_repo.get_stock_attention(business.id, "SLOW_MOVER")
        top = rows[:_SLOW_MOVER_TOP_N]
        if not top:
            return []
        names = ", ".join(r["name"] for r in top)
        return [
            InsightOut(
                category="SLOW_MOVING_PRODUCTS",
                severity="INFO",
                title="Some items haven't sold in a while",
                message=(
                    f"{len(rows)} product(s) haven't sold in over 14 days, "
                    f"including {names}. Consider a promotion or discount."
                ),
                data={"products": top, "total_count": len(rows)},
            )
        ]

    async def _low_stock_products(self, business: Business) -> list[InsightOut]:
        if not self._is_postgres():
            return []
        low = await self._dashboard_repo.get_stock_attention(business.id, "LOW_STOCK")
        out = await self._dashboard_repo.get_stock_attention(business.id, "OUT_OF_STOCK")
        combined = out + low
        if not combined:
            return []
        top = combined[:_LOW_STOCK_TOP_N]
        names = ", ".join(r["name"] for r in top)
        severity = "ALERT" if out else "WARN"
        return [
            InsightOut(
                category="LOW_STOCK_PRODUCTS",
                severity=severity,
                title="Restock needed" if out else "Stock running low",
                message=(
                    f"{len(out)} item(s) are out of stock and {len(low)} are "
                    f"running low, including {names}."
                ),
                data={"out_of_stock": out, "low_stock": low},
            )
        ]

    async def _overdue_debt(self, business: Business) -> list[InsightOut]:
        if not self._is_postgres():
            return []
        rows = await self._dashboard_repo.get_debt_priority(business.id)
        overdue = [r for r in rows if (r.get("days_overdue") or 0) > 0]
        if not overdue:
            return []
        top = overdue[:_OVERDUE_TOP_N]
        total_overdue = sum((r["debt_balance"] for r in overdue), _ZERO)
        names = ", ".join(f"{r['full_name']} ({r['debt_balance']})" for r in top)
        severity = "ALERT" if any(r["days_overdue"] >= 30 for r in top) else "WARN"
        return [
            InsightOut(
                category="OVERDUE_DEBT",
                severity=severity,
                title="Customers owe you overdue debt",
                message=(
                    f"{len(overdue)} customer(s) have overdue debt totalling "
                    f"{total_overdue}. Highest priority: {names}."
                ),
                data={"customers": top, "total_overdue": str(total_overdue)},
            )
        ]

    # ----- portable generators [work on any backend] -------------------------- #
    async def _high_risk_customers(self, business: Business) -> list[InsightOut]:
        rows, total = await self._customer_repo.search(
            business_id=business.id,
            search=None,
            status="ACTIVE",
            order="debt_desc",
            limit=_HIGH_RISK_TOP_N,
            offset=0,
            credit_level="HIGH_RISK",
        )
        blocked_rows, blocked_total = await self._customer_repo.search(
            business_id=business.id,
            search=None,
            status="ACTIVE",
            order="debt_desc",
            limit=_HIGH_RISK_TOP_N,
            offset=0,
            is_blocked=True,
        )
        if not total and not blocked_total:
            return []
        names = ", ".join(c.full_name for c in (rows + blocked_rows)[:_HIGH_RISK_TOP_N])
        return [
            InsightOut(
                category="HIGH_RISK_CUSTOMERS",
                severity="WARN" if not blocked_total else "ALERT",
                title="Customers with risky credit standing",
                message=(
                    f"{total} customer(s) are HIGH_RISK and {blocked_total} are "
                    f"BLOCKED, including {names}. Be cautious extending more debt."
                ),
                data={"high_risk_count": total, "blocked_count": blocked_total},
            )
        ]

    async def _sales_trend(self, business: Business) -> list[InsightOut]:
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        sales_today = await self._insights_repo.sales_total_for_date(business.id, today)
        sales_yesterday = await self._insights_repo.sales_total_for_date(
            business.id, yesterday
        )
        if sales_today == 0 and sales_yesterday == 0:
            return []
        if sales_yesterday == 0:
            message = f"Today's sales are {sales_today}; there were no sales yesterday to compare."
            severity = "INFO"
        else:
            change_pct = ((sales_today - sales_yesterday) / sales_yesterday) * 100
            direction = "up" if change_pct >= 0 else "down"
            message = (
                f"Today's sales ({sales_today}) are {direction} "
                f"{abs(change_pct):.0f}% compared to yesterday ({sales_yesterday})."
            )
            severity = "WARN" if change_pct <= -30 else "INFO"
        return [
            InsightOut(
                category="SALES_TREND",
                severity=severity,
                title="Today vs. yesterday",
                message=message,
                data={"sales_today": str(sales_today), "sales_yesterday": str(sales_yesterday)},
            )
        ]

    async def _expense_pressure(self, business: Business) -> list[InsightOut]:
        today = datetime.now(timezone.utc).date()
        sales_today = await self._insights_repo.sales_total_for_date(business.id, today)
        expenses_today = await self._insights_repo.expenses_total_for_date(business.id, today)
        if expenses_today == 0:
            return []
        if sales_today == 0:
            return [
                InsightOut(
                    category="EXPENSE_PRESSURE",
                    severity="WARN",
                    title="Expenses with no sales yet today",
                    message=f"You've spent {expenses_today} today with no recorded sales so far.",
                    data={"expenses_today": str(expenses_today), "sales_today": "0"},
                )
            ]
        ratio = expenses_today / sales_today
        if ratio < _EXPENSE_TO_SALES_WARN_RATIO:
            return []
        return [
            InsightOut(
                category="EXPENSE_PRESSURE",
                severity="WARN" if ratio < 1 else "ALERT",
                title="Expenses are eating into today's sales",
                message=(
                    f"Today's expenses ({expenses_today}) are "
                    f"{int(ratio * 100)}% of today's sales ({sales_today})."
                ),
                data={"expenses_today": str(expenses_today), "sales_today": str(sales_today)},
            )
        ]

    async def _cash_flow_summary(self, business: Business) -> list[InsightOut]:
        """Always returns exactly one card — the current cash position is
        useful context regardless of whether anything is "wrong", unlike
        every other generator here which only speaks up when there's
        something worth flagging."""
        today = datetime.now(timezone.utc).date()
        sales_today = await self._insights_repo.sales_total_for_date(business.id, today)
        payments_today = await self._insights_repo.payments_total_for_date(business.id, today)
        expenses_today = await self._insights_repo.expenses_total_for_date(business.id, today)
        stock_today = await self._insights_repo.stock_purchases_total_for_date(business.id, today)
        net_today = sales_today + payments_today - expenses_today - stock_today

        severity = "INFO"
        if business.cash_on_hand < 0:
            severity = "ALERT"
        elif net_today < 0:
            severity = "WARN"

        return [
            InsightOut(
                category="CASH_FLOW",
                severity=severity,
                title="Cash position",
                message=(
                    f"You currently have {business.cash_on_hand} in cash and "
                    f"{business.total_debt_out} owed to you by customers. "
                    f"Net cash movement today: {net_today:+}."
                ),
                data={
                    "cash_on_hand": str(business.cash_on_hand),
                    "total_debt_out": str(business.total_debt_out),
                    "net_today": str(net_today),
                },
            )
        ]

    async def _unusual_activity(self, business: Business) -> list[InsightOut]:
        today = datetime.now(timezone.utc).date()
        insights: list[InsightOut] = []

        expenses_today = await self._insights_repo.expenses_total_for_date(business.id, today)
        avg_daily_expense = await self._insights_repo.average_daily_expenses(
            business.id, days=30, exclude=today
        )
        if avg_daily_expense > 0 and expenses_today >= avg_daily_expense * _UNUSUAL_EXPENSE_MULTIPLIER:
            insights.append(
                InsightOut(
                    category="UNUSUAL_ACTIVITY",
                    severity="WARN",
                    title="Unusually high expenses today",
                    message=(
                        f"Today's expenses ({expenses_today}) are well above your "
                        f"typical daily average ({avg_daily_expense:.2f})."
                    ),
                    data={"expenses_today": str(expenses_today), "average_daily": str(avg_daily_expense)},
                )
            )

        max_sale_today = await self._insights_repo.max_sale_today(business.id, today)
        avg_sale = await self._insights_repo.average_sale_amount(
            business.id, days=30, exclude=today
        )
        if avg_sale > 0 and max_sale_today >= avg_sale * _UNUSUAL_SALE_MULTIPLIER:
            insights.append(
                InsightOut(
                    category="UNUSUAL_ACTIVITY",
                    severity="INFO",
                    title="Unusually large sale today",
                    message=(
                        f"A sale of {max_sale_today} today is well above your "
                        f"typical sale amount ({avg_sale:.2f})."
                    ),
                    data={"max_sale_today": str(max_sale_today), "average_sale": str(avg_sale)},
                )
            )

        return insights
