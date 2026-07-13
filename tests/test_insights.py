"""Business Insights tests.

View-backed generators (best-selling/slow-moving/low-stock products, overdue
debt) are PostgreSQL-only (see InsightsService._is_postgres) and are verified
here to gracefully return nothing on SQLite rather than raising — the actual
view-reading logic itself is already covered by DashboardRepository's own
usage elsewhere. The portable generators (high-risk customers, sales trend,
expense pressure, cash flow, unusual activity) are fully exercised here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.customer import Customer
from app.models.expense import Expense
from app.models.sale import Sale
from app.schemas.customer import CustomerCreateRequest
from app.services.customer_service import CustomerService
from app.services.insights_service import InsightsService


async def test_insights_endpoint_requires_ownership(business_ctx, owner, session):
    from app.models.user import User

    other_user = User(email="other@example.com", full_name="Other Owner", is_email_verified=True)
    session.add(other_user)
    await session.flush()
    await session.commit()

    service = InsightsService(session)
    with pytest.raises(ForbiddenError):
        await service.get_insights(other_user, business_ctx.business_id)


async def test_insights_unknown_business_raises_not_found(session, owner):
    service = InsightsService(session)
    with pytest.raises(NotFoundError):
        await service.get_insights(owner, uuid.uuid4())


async def test_insights_view_backed_generators_are_skipped_on_sqlite(business_ctx, owner, session):
    """Product/debt insights depend on PostgreSQL-only views; on SQLite they
    should simply be absent, never raise."""
    service = InsightsService(session)
    result = await service.get_insights(owner, business_ctx.business_id)
    categories = {i.category for i in result.items}
    assert "BEST_SELLING_PRODUCTS" not in categories
    assert "SLOW_MOVING_PRODUCTS" not in categories
    assert "LOW_STOCK_PRODUCTS" not in categories
    assert "OVERDUE_DEBT" not in categories


async def test_insights_returns_only_cash_flow_baseline_for_quiet_business(business_ctx, owner, session):
    """CASH_FLOW is an always-present summary card (current position, not a
    warning) — everything else is conditional on there being data to react
    to, so a brand-new business with zero activity gets just that one card.
    """
    service = InsightsService(session)
    result = await service.get_insights(owner, business_ctx.business_id)
    assert result.business_id == business_ctx.business_id
    assert [i.category for i in result.items] == ["CASH_FLOW"]
    assert result.items[0].severity == "INFO"
    assert result.generated_at is not None


async def test_high_risk_customers_insight(business_ctx, owner, session):
    cust_service = CustomerService(business_ctx)
    risky = await cust_service.create(CustomerCreateRequest(full_name="Risky Co"))
    row = await session.get(Customer, risky.id)
    row.credit_level = "HIGH_RISK"
    await session.flush()
    await session.commit()

    service = InsightsService(session)
    result = await service.get_insights(owner, business_ctx.business_id)
    matching = [i for i in result.items if i.category == "HIGH_RISK_CUSTOMERS"]
    assert len(matching) == 1
    assert "Risky Co" in matching[0].message
    assert matching[0].severity == "WARN"


async def test_blocked_customers_escalate_severity_to_alert(business_ctx, owner, session):
    cust_service = CustomerService(business_ctx)
    blocked = await cust_service.create(CustomerCreateRequest(full_name="Blocked Co"))
    row = await session.get(Customer, blocked.id)
    row.credit_level = "BLOCKED"
    row.is_credit_blocked = True
    await session.flush()
    await session.commit()

    service = InsightsService(session)
    result = await service.get_insights(owner, business_ctx.business_id)
    matching = [i for i in result.items if i.category == "HIGH_RISK_CUSTOMERS"]
    assert matching[0].severity == "ALERT"


async def test_sales_trend_insight_compares_today_and_yesterday(business_ctx, owner, session):
    biz_id = business_ctx.business_id
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1, hours=2)
    session.add(Sale(business_id=biz_id, payment_type_id=1, total_amount=Decimal("10000"),
                      total_buying_cost=Decimal("6000"), outstanding_amount=Decimal("0"),
                      occurred_at=now, receipt_number="T1", is_correction=False))
    session.add(Sale(business_id=biz_id, payment_type_id=1, total_amount=Decimal("2000"),
                      total_buying_cost=Decimal("1000"), outstanding_amount=Decimal("0"),
                      occurred_at=yesterday, receipt_number="Y1", is_correction=False))
    await session.flush()
    await session.commit()

    service = InsightsService(session)
    result = await service.get_insights(owner, biz_id)
    trend = next(i for i in result.items if i.category == "SALES_TREND")
    assert "up" in trend.message
    assert trend.data["sales_today"] == "10000.00" or trend.data["sales_today"] == "10000"


async def test_sales_trend_large_drop_is_flagged_as_warning(business_ctx, owner, session):
    biz_id = business_ctx.business_id
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1, hours=2)
    session.add(Sale(business_id=biz_id, payment_type_id=1, total_amount=Decimal("500"),
                      total_buying_cost=Decimal("300"), outstanding_amount=Decimal("0"),
                      occurred_at=now, receipt_number="T1", is_correction=False))
    session.add(Sale(business_id=biz_id, payment_type_id=1, total_amount=Decimal("10000"),
                      total_buying_cost=Decimal("6000"), outstanding_amount=Decimal("0"),
                      occurred_at=yesterday, receipt_number="Y1", is_correction=False))
    await session.flush()
    await session.commit()

    service = InsightsService(session)
    result = await service.get_insights(owner, biz_id)
    trend = next(i for i in result.items if i.category == "SALES_TREND")
    assert trend.severity == "WARN"
    assert "down" in trend.message


async def test_expense_pressure_insight(business_ctx, owner, session):
    biz_id = business_ctx.business_id
    now = datetime.now(timezone.utc)
    session.add(Sale(business_id=biz_id, payment_type_id=1, total_amount=Decimal("1000"),
                      total_buying_cost=Decimal("600"), outstanding_amount=Decimal("0"),
                      occurred_at=now, receipt_number="S1", is_correction=False))
    session.add(Expense(business_id=biz_id, category_id=1, amount=Decimal("900"), occurred_at=now, is_correction=False))
    await session.flush()
    await session.commit()

    service = InsightsService(session)
    result = await service.get_insights(owner, biz_id)
    pressure = next(i for i in result.items if i.category == "EXPENSE_PRESSURE")
    assert pressure.severity == "WARN"


async def test_expense_with_no_sales_is_flagged(business_ctx, owner, session):
    biz_id = business_ctx.business_id
    now = datetime.now(timezone.utc)
    session.add(Expense(business_id=biz_id, category_id=1, amount=Decimal("5000"), occurred_at=now, is_correction=False))
    await session.flush()
    await session.commit()

    service = InsightsService(session)
    result = await service.get_insights(owner, biz_id)
    pressure = next(i for i in result.items if i.category == "EXPENSE_PRESSURE")
    assert "no recorded sales" in pressure.message


async def test_cash_flow_summary_always_present_when_theres_activity(business_ctx, owner, session):
    biz_id = business_ctx.business_id
    now = datetime.now(timezone.utc)
    session.add(Sale(business_id=biz_id, payment_type_id=1, total_amount=Decimal("3000"),
                      total_buying_cost=Decimal("2000"), outstanding_amount=Decimal("0"),
                      occurred_at=now, receipt_number="S1", is_correction=False))
    await session.flush()
    await session.commit()

    service = InsightsService(session)
    result = await service.get_insights(owner, biz_id)
    cash_flow = next(i for i in result.items if i.category == "CASH_FLOW")
    assert "cash" in cash_flow.message.lower()
    assert cash_flow.data["net_today"] is not None


async def test_unusual_activity_flags_expense_spike(business_ctx, owner, session):
    biz_id = business_ctx.business_id
    now = datetime.now(timezone.utc)
    # baseline: small expenses over the past several days
    for i in range(5):
        session.add(
            Expense(
                business_id=biz_id, category_id=1, amount=Decimal("100"),
                occurred_at=now - timedelta(days=i + 1), is_correction=False,
            )
        )
    # today: a huge spike
    session.add(Expense(business_id=biz_id, category_id=1, amount=Decimal("5000"), occurred_at=now, is_correction=False))
    await session.flush()
    await session.commit()

    service = InsightsService(session)
    result = await service.get_insights(owner, biz_id)
    unusual = [i for i in result.items if i.category == "UNUSUAL_ACTIVITY"]
    assert any("expenses" in i.message.lower() for i in unusual)


async def test_insights_sorted_by_severity(business_ctx, owner, session):
    biz_id = business_ctx.business_id
    cust_service = CustomerService(business_ctx)
    blocked = await cust_service.create(CustomerCreateRequest(full_name="Blocked Co"))
    row = await session.get(Customer, blocked.id)
    row.credit_level = "BLOCKED"
    row.is_credit_blocked = True
    await session.flush()
    now = datetime.now(timezone.utc)
    session.add(Sale(business_id=biz_id, payment_type_id=1, total_amount=Decimal("1000"),
                      total_buying_cost=Decimal("600"), outstanding_amount=Decimal("0"),
                      occurred_at=now, receipt_number="S1", is_correction=False))
    await session.flush()
    await session.commit()

    service = InsightsService(session)
    result = await service.get_insights(owner, biz_id)
    severities = [i.severity for i in result.items]
    rank = {"ALERT": 0, "WARN": 1, "INFO": 2}
    ranks = [rank[s] for s in severities]
    assert ranks == sorted(ranks)
