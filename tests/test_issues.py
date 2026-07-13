"""Issue (Decision Engine) module tests — filters by status, severity,
confidence, customer, product, and the ``resolved`` convenience flag.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models.issue import Issue, IssueType
from app.schemas.customer import CustomerCreateRequest
from app.schemas.issue import IssueUpdateRequest
from app.schemas.product import ProductCreateRequest
from app.services.customer_service import CustomerService
from app.services.issue_service import IssueService
from app.services.product_service import ProductService


async def _make_issue(session, business_ctx, *, code, severity="WARN", confidence=3, customer_id=None, product_id=None, status="ACTIVE"):
    itype = (await session.execute(select(IssueType).where(IssueType.code == code))).scalar_one()
    issue = Issue(
        business_id=business_ctx.business_id,
        issue_type_id=itype.id,
        ref_customer_id=customer_id,
        ref_product_id=product_id,
        confidence_level=confidence,
        severity=severity,
        headline=f"Test issue {code}",
        status=status,
    )
    session.add(issue)
    await session.flush()
    await session.commit()
    return issue


async def test_filter_by_status(business_ctx, session):
    await _make_issue(session, business_ctx, code="STOCK_FINISHING", status="ACTIVE")
    await _make_issue(session, business_ctx, code="STOCK_FINISHING", status="RESOLVED")
    service = IssueService(business_ctx)
    active = await service.list_issues(status="ACTIVE", min_confidence=None, severity=None, limit=20, offset=0)
    resolved = await service.list_issues(status="RESOLVED", min_confidence=None, severity=None, limit=20, offset=0)
    assert active.total == 1
    assert resolved.total == 1


async def test_filter_by_severity_and_confidence(business_ctx, session):
    await _make_issue(session, business_ctx, code="LOW_MARGIN", severity="WARN", confidence=2)
    await _make_issue(session, business_ctx, code="LOW_MARGIN", severity="ALERT", confidence=4)
    service = IssueService(business_ctx)
    alerts = await service.list_issues(status="ACTIVE", min_confidence=None, severity="ALERT", limit=20, offset=0)
    high_conf = await service.list_issues(status="ACTIVE", min_confidence=4, severity=None, limit=20, offset=0)
    assert alerts.total == 1
    assert high_conf.total == 1


async def test_filter_by_customer(business_ctx, session):
    c1 = await CustomerService(business_ctx).create(CustomerCreateRequest(full_name="C1"))
    c2 = await CustomerService(business_ctx).create(CustomerCreateRequest(full_name="C2"))
    await _make_issue(session, business_ctx, code="DEBT_OVERDUE", customer_id=c1.id)
    await _make_issue(session, business_ctx, code="DEBT_OVERDUE", customer_id=c2.id)
    service = IssueService(business_ctx)
    result = await service.list_issues(
        status="ACTIVE", min_confidence=None, severity=None, ref_customer_id=c1.id, limit=20, offset=0
    )
    assert result.total == 1
    assert result.items[0].ref_customer_id == c1.id


async def test_filter_by_product(business_ctx, session):
    p1 = (await ProductService(business_ctx).create(ProductCreateRequest(name="P1"))).product
    p2 = (await ProductService(business_ctx).create(ProductCreateRequest(name="P2"))).product
    await _make_issue(session, business_ctx, code="STOCK_FINISHING", product_id=p1.id)
    await _make_issue(session, business_ctx, code="STOCK_FINISHING", product_id=p2.id)
    service = IssueService(business_ctx)
    result = await service.list_issues(
        status="ACTIVE", min_confidence=None, severity=None, ref_product_id=p1.id, limit=20, offset=0
    )
    assert result.total == 1
    assert result.items[0].ref_product_id == p1.id


async def test_resolved_filter_overrides_default_status(business_ctx, session):
    await _make_issue(session, business_ctx, code="EXPENSE_PRESSURE", status="ACTIVE")
    await _make_issue(session, business_ctx, code="EXPENSE_PRESSURE", status="RESOLVED")
    service = IssueService(business_ctx)
    # even with the router's default status="ACTIVE", resolved=True should
    # still surface the resolved one (see IssueRepository.list_issues).
    resolved_only = await service.list_issues(
        status="ACTIVE", min_confidence=None, severity=None, resolved=True, limit=20, offset=0
    )
    not_resolved = await service.list_issues(
        status="ACTIVE", min_confidence=None, severity=None, resolved=False, limit=20, offset=0
    )
    assert resolved_only.total == 1
    assert resolved_only.items[0].status == "RESOLVED"
    assert not_resolved.total == 1
    assert not_resolved.items[0].status == "ACTIVE"


async def test_update_issue_status(business_ctx, session):
    issue = await _make_issue(session, business_ctx, code="LOW_MARGIN")
    service = IssueService(business_ctx)
    updated = await service.update_status(issue.id, IssueUpdateRequest(status="IGNORED"))
    assert updated.status == "IGNORED"
    assert updated.resolved_at is not None
