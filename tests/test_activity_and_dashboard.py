"""Unified activity feed tests — today/yesterday/custom range, pagination,
merging across sales, expenses, stock purchases, capital transactions, and
customer payments.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.business import CapitalTransaction
from app.models.expense import Expense
from app.models.payment import CustomerPayment
from app.models.sale import Sale
from app.models.stock_purchase import StockPurchase
from app.services.customer_service import CustomerService
from app.services.dashboard_service import DashboardService
from app.schemas.customer import CustomerCreateRequest


async def _seed_mixed_activity(business_ctx, session):
    biz_id = business_ctx.business_id
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1, hours=2)

    customer = await CustomerService(business_ctx).create(
        CustomerCreateRequest(full_name="Activity Customer")
    )

    session.add(
        Sale(
            business_id=biz_id, payment_type_id=1, total_amount=Decimal("5000"),
            total_buying_cost=Decimal("3000"), outstanding_amount=Decimal("0"),
            occurred_at=now, receipt_number="R1", is_correction=False,
        )
    )
    session.add(
        Sale(
            business_id=biz_id, payment_type_id=1, total_amount=Decimal("2000"),
            total_buying_cost=Decimal("1000"), outstanding_amount=Decimal("0"),
            occurred_at=yesterday, receipt_number="R2", is_correction=False,
        )
    )
    session.add(
        Expense(business_id=biz_id, category_id=1, amount=Decimal("800"), occurred_at=now, is_correction=False)
    )
    session.add(
        StockPurchase(business_id=biz_id, total_buying_cost=Decimal("1500"), occurred_at=now, is_correction=False)
    )
    session.add(
        CapitalTransaction(business_id=biz_id, type_id=2, amount=Decimal("10000"), occurred_at=now, is_correction=False)
    )
    session.add(
        CustomerPayment(business_id=biz_id, customer_id=customer.id, amount=Decimal("3000"), occurred_at=now, is_correction=False)
    )
    await session.flush()
    await session.commit()


async def test_activity_period_today_excludes_yesterday(business_ctx, session):
    await _seed_mixed_activity(business_ctx, session)
    dashboard = DashboardService(business_ctx)
    result = await dashboard.get_activity(period="today")
    types = {item.activity_type for item in result.items}
    assert types == {"SALE", "EXPENSE", "STOCK_PURCHASE", "CAPITAL_TRANSACTION", "CUSTOMER_PAYMENT"}
    # today's sale is 5000, not the 2000 one from yesterday
    sale_amounts = [item.amount for item in result.items if item.activity_type == "SALE"]
    assert sale_amounts == [Decimal("5000.00")] or sale_amounts == [Decimal("5000")]


async def test_activity_period_yesterday_only_has_yesterdays_sale(business_ctx, session):
    await _seed_mixed_activity(business_ctx, session)
    dashboard = DashboardService(business_ctx)
    result = await dashboard.get_activity(period="yesterday")
    assert result.total == 1
    assert result.items[0].activity_type == "SALE"
    assert result.items[0].amount in (Decimal("2000"), Decimal("2000.00"))


async def test_activity_custom_range_open_ended(business_ctx, session):
    await _seed_mixed_activity(business_ctx, session)
    dashboard = DashboardService(business_ctx)
    result = await dashboard.get_activity(period="custom")
    assert result.total == 6  # every seeded row, unions correctly


async def test_activity_is_sorted_newest_first(business_ctx, session):
    await _seed_mixed_activity(business_ctx, session)
    dashboard = DashboardService(business_ctx)
    result = await dashboard.get_activity(period="custom", limit=10, offset=0)
    occurred_ats = [item.occurred_at for item in result.items]
    assert occurred_ats == sorted(occurred_ats, reverse=True)


async def test_activity_pagination(business_ctx, session):
    await _seed_mixed_activity(business_ctx, session)
    dashboard = DashboardService(business_ctx)
    page1 = await dashboard.get_activity(period="custom", limit=2, offset=0)
    page2 = await dashboard.get_activity(period="custom", limit=2, offset=2)
    assert page1.total == 6
    assert len(page1.items) == 2
    assert len(page2.items) == 2
    assert {i.record_id for i in page1.items}.isdisjoint({i.record_id for i in page2.items})


async def test_today_extras_profit_and_stock_purchases(business_ctx, session):
    await _seed_mixed_activity(business_ctx, session)
    from app.repositories.dashboard_repository import DashboardRepository

    extras = await DashboardRepository(session).get_today_extras(business_ctx.business_id)
    # today's sale profit = 5000 - 3000 = 2000
    assert extras["profit_today"] == Decimal("2000") or extras["profit_today"] == Decimal("2000.00")
    assert extras["stock_purchases_today"] == Decimal("1500") or extras["stock_purchases_today"] == Decimal("1500.00")
