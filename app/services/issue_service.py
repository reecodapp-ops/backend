"""Issue / Decision Engine service.

Runs phase-1 rules over a business's state and writes/supersedes ``issues`` rows.
Rules implemented here (all phase 1 — the data they need is already available
from the modules built so far):

  * STOCK_FINISHING   — product.stock_on_hand <= low_stock_level (LOW_STOCK
                        bucket of v_stock_attention). WARN.
  * DEBT_OVERDUE      — customer.debt_balance > 0 AND days since
                        oldest_unpaid_at > OVERDUE_DAYS. WARN.
  * LOW_MARGIN        — product margin_pct < LOW_MARGIN_PCT (from
                        v_product_margin_30d). WARN.
  * EXPENSE_PRESSURE  — last-30-days total expenses / last-30-days revenue
                        > EXPENSE_RATIO_THRESHOLD. WARN.

The engine is convergent: when re-run on the same data it produces the same
issues. When the underlying condition disappears, the previously-active issue is
SUPERSEDED rather than deleted (audit trail). When the condition still holds,
the existing ACTIVE issue is left in place. Counts in the recompute response
make the worker's effect observable.

No analytics in Python: the heavy lifting (margin %, aging, days since sale) is
done by the dashboard views; this service just reads those views and applies
threshold tests.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_context import BusinessContext
from app.core.exceptions import NotFoundError, ValidationError
from app.repositories.issue_repository import IssueRepository
from app.schemas.issue import (
    IssueListOut,
    IssueOut,
    IssueUpdateRequest,
    RecomputeResponse,
)

# Thresholds. Conservative defaults; would be moved to BusinessSubscription /
# config in a real deployment.
OVERDUE_DAYS = 14
LOW_MARGIN_PCT = Decimal("15")  # < 15% gross margin -> low margin
EXPENSE_RATIO_THRESHOLD = Decimal("0.6")  # expenses > 60% of revenue -> pressure


class IssueEngine:
    """Stateless helper that builds candidate issues from a business's data.

    The ``recompute`` method on the service applies these candidates against the
    persisted issues, inserting new ones and superseding ones whose conditions
    no longer hold.
    """

    @staticmethod
    def _biz_param(session: AsyncSession, business_id: uuid.UUID) -> str:
        """Same UUID dialect bridging as DashboardRepository._biz_param."""
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            return business_id.hex
        return str(business_id)

    @staticmethod
    async def stock_finishing(
        session: AsyncSession, business_id: uuid.UUID, types: dict
    ) -> list[dict]:
        """Each product with stock <= low_stock_level produces one card."""
        rows = (
            await session.execute(
                text(
                    "SELECT product_id, name, stock_on_hand, low_stock_level "
                    "FROM v_stock_attention "
                    "WHERE business_id = :biz "
                    "AND stock_status IN ('LOW_STOCK','OUT_OF_STOCK')"
                ),
                {"biz": IssueEngine._biz_param(session, business_id)},
            )
        ).mappings().all()
        candidates = []
        for r in rows:
            stock = Decimal(str(r["stock_on_hand"]))
            low = Decimal(str(r["low_stock_level"]))
            severity = "ALERT" if stock <= 0 else "WARN"
            candidates.append(
                {
                    "type_code": "STOCK_FINISHING",
                    "ref_product_id": uuid.UUID(str(r["product_id"])) if not isinstance(r["product_id"], uuid.UUID) else r["product_id"],
                    "ref_customer_id": None,
                    "ref_sale_id": None,
                    "confidence_level": 4,
                    "severity": severity,
                    "headline": f"{r['name']} is running low",
                    "body_text": (
                        f"Only {stock} left in stock (reorder level "
                        f"is {low}). Restock before you run out of sales."
                    ),
                    "suggested_action": "Plan a stock purchase soon.",
                    "data_snapshot": {
                        "product_name": r["name"],
                        "stock_on_hand": str(stock),
                        "low_stock_level": str(low),
                    },
                }
            )
        return candidates

    @staticmethod
    async def debt_overdue(
        session: AsyncSession, business_id: uuid.UUID, types: dict
    ) -> list[dict]:
        rows = (
            await session.execute(
                text(
                    "SELECT customer_id, full_name, debt_balance, days_overdue "
                    "FROM v_debt_priority WHERE business_id = :biz "
                    "AND days_overdue > :days"
                ),
                {
                    "biz": IssueEngine._biz_param(session, business_id),
                    "days": OVERDUE_DAYS,
                },
            )
        ).mappings().all()
        candidates = []
        for r in rows:
            severity = "ALERT" if r["days_overdue"] >= 30 else "WARN"
            candidates.append(
                {
                    "type_code": "DEBT_OVERDUE",
                    "ref_product_id": None,
                    "ref_customer_id": uuid.UUID(str(r["customer_id"])) if not isinstance(r["customer_id"], uuid.UUID) else r["customer_id"],
                    "ref_sale_id": None,
                    "confidence_level": 4,
                    "severity": severity,
                    "headline": (
                        f"{r['full_name']} owes {r['debt_balance']} — "
                        f"{r['days_overdue']} days overdue"
                    ),
                    "body_text": "Consider following up about this debt.",
                    "suggested_action": "Send a friendly reminder.",
                    "data_snapshot": {
                        "customer_name": r["full_name"],
                        "debt_balance": str(r["debt_balance"]),
                        "days_overdue": int(r["days_overdue"]),
                    },
                }
            )
        return candidates

    @staticmethod
    async def low_margin(
        session: AsyncSession, business_id: uuid.UUID, types: dict
    ) -> list[dict]:
        rows = (
            await session.execute(
                text(
                    "SELECT product_id, product_name, margin_pct, revenue "
                    "FROM v_product_margin_30d "
                    "WHERE business_id = :biz AND margin_pct IS NOT NULL "
                    "AND margin_pct < :thr"
                ),
                {
                    "biz": IssueEngine._biz_param(session, business_id),
                    "thr": float(LOW_MARGIN_PCT),
                },
            )
        ).mappings().all()
        candidates = []
        for r in rows:
            candidates.append(
                {
                    "type_code": "LOW_MARGIN",
                    "ref_product_id": uuid.UUID(str(r["product_id"])) if not isinstance(r["product_id"], uuid.UUID) else r["product_id"],
                    "ref_customer_id": None,
                    "ref_sale_id": None,
                    "confidence_level": 3,
                    "severity": "WARN",
                    "headline": (
                        f"{r['product_name']} is moving but earning little "
                        f"({r['margin_pct']}% margin)"
                    ),
                    "body_text": (
                        f"In the last 30 days you sold {r['revenue']} worth "
                        f"with only {r['margin_pct']}% margin. Either raise "
                        f"the price or check the buying cost."
                    ),
                    "suggested_action": "Review the buying price and selling price.",
                    "data_snapshot": {
                        "product_name": r["product_name"],
                        "margin_pct": str(r["margin_pct"]),
                        "revenue": str(r["revenue"]),
                    },
                }
            )
        return candidates

    @staticmethod
    async def expense_pressure(
        session: AsyncSession, business_id: uuid.UUID, types: dict
    ) -> list[dict]:
        """Single business-wide card when expenses-to-revenue ratio is high."""
        biz_param = IssueEngine._biz_param(session, business_id)
        exp_row = (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(total_spent), 0) AS total "
                    "FROM v_expense_pressure_30d WHERE business_id = :biz"
                ),
                {"biz": biz_param},
            )
        ).mappings().first()
        rev_row = (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(revenue), 0) AS total "
                    "FROM v_product_margin_30d WHERE business_id = :biz"
                ),
                {"biz": biz_param},
            )
        ).mappings().first()
        expenses = Decimal(str(exp_row["total"] if exp_row else 0))
        revenue = Decimal(str(rev_row["total"] if rev_row else 0))
        if revenue <= 0:
            return []
        ratio = expenses / revenue
        if ratio < EXPENSE_RATIO_THRESHOLD:
            return []
        return [
            {
                "type_code": "EXPENSE_PRESSURE",
                "ref_product_id": None,
                "ref_customer_id": None,
                "ref_sale_id": None,
                "confidence_level": 3,
                "severity": "WARN",
                "headline": (
                    f"Expenses are eating {int(ratio * 100)}% of your sales"
                ),
                "body_text": (
                    f"Last 30 days: expenses {expenses}, revenue {revenue}. "
                    f"Look at where the money is going."
                ),
                "suggested_action": "Open the expense pressure dashboard.",
                "data_snapshot": {
                    "expenses_30d": str(expenses),
                    "revenue_30d": str(revenue),
                    "ratio": str(ratio.quantize(Decimal("0.01"))),
                },
            }
        ]


class IssueService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = IssueRepository(ctx.session)

    async def list_issues(
        self,
        *,
        status: str | None,
        min_confidence: int | None,
        severity: str | None,
        ref_customer_id: uuid.UUID | None = None,
        ref_product_id: uuid.UUID | None = None,
        resolved: bool | None = None,
        limit: int,
        offset: int,
    ) -> IssueListOut:
        rows, total = await self._repo.list_issues(
            business_id=self._ctx.business_id,
            status=status,
            min_confidence=min_confidence,
            severity=severity,
            ref_customer_id=ref_customer_id,
            ref_product_id=ref_product_id,
            resolved=resolved,
            limit=limit,
            offset=offset,
        )
        return IssueListOut(
            items=[self._to_out(i, t) for i, t in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def update_status(
        self, issue_id: uuid.UUID, data: IssueUpdateRequest
    ) -> IssueOut:
        result = await self._repo.get_with_type(self._ctx.business_id, issue_id)
        if result is None:
            raise NotFoundError("Issue not found.", code="issue_not_found")
        issue, itype = result
        if issue.status in ("RESOLVED", "IGNORED", "SUPERSEDED"):
            raise ValidationError(
                f"Issue is already {issue.status.lower()}.",
                code="issue_not_active",
            )
        await self._repo.update_status(issue, data.status)
        return self._to_out(issue, itype)

    async def recompute(self) -> RecomputeResponse:
        return await recompute_for_business(
            self._ctx.session, self._ctx.business_id
        )

    @staticmethod
    def _to_out(issue, itype) -> IssueOut:
        return IssueOut(
            id=issue.id,
            business_id=issue.business_id,
            issue_type_id=issue.issue_type_id,
            issue_type_code=itype.code,
            ref_product_id=issue.ref_product_id,
            ref_customer_id=issue.ref_customer_id,
            ref_sale_id=issue.ref_sale_id,
            confidence_level=issue.confidence_level,
            severity=issue.severity,
            headline=issue.headline,
            body_text=issue.body_text,
            suggested_action=issue.suggested_action,
            data_snapshot=issue.data_snapshot,
            status=issue.status,
            resolved_at=issue.resolved_at,
            created_at=issue.created_at,
            updated_at=issue.updated_at,
        )


# Module-level so the worker can call it without instantiating BusinessContext.
async def recompute_for_business(
    session: AsyncSession, business_id: uuid.UUID
) -> RecomputeResponse:
    """Run all phase-1 rules and reconcile the issues table for one business.

    Returns counts (created / superseded / unchanged) so the worker log is
    meaningful. Inserts new issues, supersedes existing ACTIVE issues whose
    conditions no longer hold, and leaves matching ACTIVE issues alone.
    """
    repo = IssueRepository(session)
    types = await repo.map_types_by_code()
    if not types:
        # No seeded types — engine is a no-op until they exist.
        return RecomputeResponse(created=0, superseded=0, unchanged=0)

    # Gather candidates from every rule.
    candidates: list[dict] = []
    candidates.extend(await IssueEngine.stock_finishing(session, business_id, types))
    candidates.extend(await IssueEngine.debt_overdue(session, business_id, types))
    candidates.extend(await IssueEngine.low_margin(session, business_id, types))
    candidates.extend(await IssueEngine.expense_pressure(session, business_id, types))

    existing = await repo.get_active_issues_by_keys(business_id)
    # Build a candidate-key set for set-difference operations.
    candidate_keys: set[tuple] = set()
    for c in candidates:
        type_id = types[c["type_code"]].id
        candidate_keys.add(
            (type_id, c["ref_product_id"], c["ref_customer_id"], c["ref_sale_id"])
        )

    created = 0
    unchanged = 0
    superseded = 0

    for c in candidates:
        itype = types[c["type_code"]]
        key = (itype.id, c["ref_product_id"], c["ref_customer_id"], c["ref_sale_id"])
        if key in existing:
            unchanged += 1
            continue
        await repo.insert_issue(
            business_id=business_id,
            issue_type_id=itype.id,
            ref_product_id=c["ref_product_id"],
            ref_customer_id=c["ref_customer_id"],
            ref_sale_id=c["ref_sale_id"],
            confidence_level=c["confidence_level"],
            severity=c["severity"],
            headline=c["headline"],
            body_text=c["body_text"],
            suggested_action=c["suggested_action"],
            data_snapshot=c["data_snapshot"],
        )
        created += 1

    # Anything ACTIVE whose key is no longer a candidate gets superseded.
    for key, issue in existing.items():
        if key not in candidate_keys:
            await repo.supersede(issue)
            superseded += 1

    await session.flush()
    return RecomputeResponse(
        created=created, superseded=superseded, unchanged=unchanged
    )
