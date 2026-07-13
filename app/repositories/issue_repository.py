"""Issue repository.

Persists and queries the ``issues`` table written by the Decision Engine. The
engine itself lives in the service layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.issue import Issue, IssueType


class IssueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----- lookups ---------------------------------------------------------- #
    async def get_type_by_code(self, code: str) -> IssueType | None:
        result = await self._session.execute(
            select(IssueType).where(IssueType.code == code)
        )
        return result.scalar_one_or_none()

    async def map_types_by_code(self) -> dict[str, IssueType]:
        result = await self._session.execute(select(IssueType))
        return {t.code: t for t in result.scalars().all()}

    # ----- list ------------------------------------------------------------- #
    async def list_issues(
        self,
        *,
        business_id: uuid.UUID,
        status: str | None,
        min_confidence: int | None,
        severity: str | None,
        ref_customer_id: uuid.UUID | None = None,
        ref_product_id: uuid.UUID | None = None,
        resolved: bool | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[Issue, IssueType]], int]:
        conditions = [Issue.business_id == business_id]
        if resolved is True:
            conditions.append(Issue.status == "RESOLVED")
        elif resolved is False:
            conditions.append(Issue.status != "RESOLVED")
        elif status is not None:
            # Only applied when `resolved` wasn't explicitly given — the two
            # would otherwise contradict each other (e.g. status=ACTIVE and
            # resolved=true can never both be true).
            conditions.append(Issue.status == status)
        if min_confidence is not None:
            conditions.append(Issue.confidence_level >= min_confidence)
        if severity is not None:
            conditions.append(Issue.severity == severity)
        if ref_customer_id is not None:
            conditions.append(Issue.ref_customer_id == ref_customer_id)
        if ref_product_id is not None:
            conditions.append(Issue.ref_product_id == ref_product_id)

        base = (
            select(Issue, IssueType)
            .join(IssueType, IssueType.id == Issue.issue_type_id)
            .where(*conditions)
        )
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = (
            base.order_by(
                # ALERT first, then WARN, then INFO (manual ordering for
                # cross-dialect portability).
                func.lower(Issue.severity).desc(),
                Issue.confidence_level.desc(),
                Issue.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(i, t) for i, t in rows], total

    async def get_with_type(
        self, business_id: uuid.UUID, issue_id: uuid.UUID
    ) -> tuple[Issue, IssueType] | None:
        result = await self._session.execute(
            select(Issue, IssueType)
            .join(IssueType, IssueType.id == Issue.issue_type_id)
            .where(Issue.id == issue_id, Issue.business_id == business_id)
        )
        row = result.first()
        if row is None:
            return None
        return row[0], row[1]

    async def update_status(
        self, issue: Issue, new_status: str
    ) -> Issue:
        issue.status = new_status
        if new_status in ("RESOLVED", "IGNORED"):
            issue.resolved_at = datetime.now(timezone.utc)
        issue.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return issue

    # ----- engine-side writes ---------------------------------------------- #
    async def get_active_issues_by_keys(
        self, business_id: uuid.UUID
    ) -> dict[tuple, Issue]:
        """Map active issues by their natural key (type_id + ref_*_id) so the
        engine can decide whether to supersede or leave alone."""
        result = await self._session.execute(
            select(Issue).where(
                Issue.business_id == business_id,
                Issue.status == "ACTIVE",
            )
        )
        out: dict[tuple, Issue] = {}
        for issue in result.scalars().all():
            key = (
                issue.issue_type_id,
                issue.ref_product_id,
                issue.ref_customer_id,
                issue.ref_sale_id,
            )
            out[key] = issue
        return out

    async def insert_issue(
        self,
        *,
        business_id: uuid.UUID,
        issue_type_id: int,
        ref_product_id: uuid.UUID | None,
        ref_customer_id: uuid.UUID | None,
        ref_sale_id: uuid.UUID | None,
        confidence_level: int,
        severity: str,
        headline: str,
        body_text: str | None,
        suggested_action: str | None,
        data_snapshot: dict[str, Any],
    ) -> Issue:
        issue = Issue(
            business_id=business_id,
            issue_type_id=issue_type_id,
            ref_product_id=ref_product_id,
            ref_customer_id=ref_customer_id,
            ref_sale_id=ref_sale_id,
            confidence_level=confidence_level,
            severity=severity,
            headline=headline,
            body_text=body_text,
            suggested_action=suggested_action,
            data_snapshot=data_snapshot,
            status="ACTIVE",
        )
        self._session.add(issue)
        await self._session.flush()
        return issue

    async def supersede(self, issue: Issue) -> None:
        issue.status = "SUPERSEDED"
        issue.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    # ----- Customer Credibility Engine timeline [NEW] ------------------------ #
    # The five issue_types codes the Customer Credibility Engine raises
    # (recompute_customer_credit_score / raise_overdue_debt_issues). Kept here
    # rather than in the service so the "what counts as a credit event" list
    # has one home.
    CREDIT_EVENT_CODES = (
        "CUSTOMER_HIGH_RISK",
        "CUSTOMER_EXCELLENT",
        "CREDIT_LIMIT_REACHED",
        "PAYMENT_OVERDUE",
        "REPEATED_DEFAULT",
    )

    async def list_credit_events(
        self,
        *,
        business_id: uuid.UUID,
        customer_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[Issue, IssueType, str]], int]:
        """Every issue raised by the Customer Credibility Engine, newest first.

        This is DukaMate's durable "credit score history": each row is a
        moment where a customer's standing changed enough to cross a
        threshold (became high risk, became excellent, hit their limit,
        repeated a late payment, or fell overdue), together with the
        score/level snapshot captured in ``data_snapshot`` at that moment.

        Returns ``(issue, issue_type, customer_full_name)`` tuples.
        """
        from app.models.customer import Customer

        conditions = [
            Issue.business_id == business_id,
            Issue.ref_customer_id.is_not(None),
            IssueType.code.in_(self.CREDIT_EVENT_CODES),
        ]
        if customer_id is not None:
            conditions.append(Issue.ref_customer_id == customer_id)

        base = (
            select(Issue, IssueType, Customer.full_name)
            .join(IssueType, IssueType.id == Issue.issue_type_id)
            .join(Customer, Customer.id == Issue.ref_customer_id)
            .where(*conditions)
        )
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = base.order_by(Issue.created_at.desc()).limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).all()
        return [(i, t, name) for i, t, name in rows], total

    async def flush(self) -> None:
        await self._session.flush()
