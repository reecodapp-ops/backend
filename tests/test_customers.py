"""Customer module tests — CRUD, filtering, pagination, and the Customer
Credibility Engine (credit profile, simulation, ledger, payment history,
credit board, credit-history timeline).

Trigger-computed state (debt_balance, customer_debt_ledger rows, credit
score/level) is seeded directly via the ORM rather than produced by a real
debt sale + payment, since those triggers only exist on PostgreSQL — see
conftest.py's module docstring.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.exceptions import NotFoundError, ValidationError
from app.models.customer import Customer
from app.models.sale import CustomerDebtLedger, Sale
from app.schemas.customer import CustomerCreateRequest, CustomerUpdateRequest
from app.services.customer_service import CustomerService


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
async def test_create_customer(business_ctx):
    service = CustomerService(business_ctx)
    out = await service.create(CustomerCreateRequest(full_name="Jane Doe", phone_number="+256700111222"))
    assert out.full_name == "Jane Doe"
    assert out.status == "ACTIVE"
    assert out.debt_balance == Decimal("0")
    assert out.credit_level == "UNSCORED"


async def test_get_customer(business_ctx):
    service = CustomerService(business_ctx)
    created = await service.create(CustomerCreateRequest(full_name="Amos"))
    fetched = await service.get(created.id)
    assert fetched.id == created.id


async def test_get_customer_not_found_raises(business_ctx):
    service = CustomerService(business_ctx)
    with pytest.raises(NotFoundError):
        await service.get(uuid.uuid4())


async def test_update_customer(business_ctx):
    service = CustomerService(business_ctx)
    created = await service.create(CustomerCreateRequest(full_name="Old Name"))
    updated = await service.update(
        created.id, CustomerUpdateRequest(full_name="New Name", notes="VIP")
    )
    assert updated.full_name == "New Name"
    assert updated.notes == "VIP"


async def test_archive_customer_is_soft_delete(business_ctx):
    service = CustomerService(business_ctx)
    created = await service.create(CustomerCreateRequest(full_name="To Archive"))
    archived = await service.archive(created.id)
    assert archived.status == "ARCHIVED"
    # still readable — soft delete preserves the row
    fetched = await service.get(created.id)
    assert fetched.status == "ARCHIVED"


# --------------------------------------------------------------------------- #
# Search / filtering / pagination
# --------------------------------------------------------------------------- #
async def test_search_by_name(business_ctx):
    service = CustomerService(business_ctx)
    await service.create(CustomerCreateRequest(full_name="Alice Wanjiru"))
    await service.create(CustomerCreateRequest(full_name="Bob Otieno"))
    result = await service.search(
        search="Alice", status="ACTIVE", order="recent", limit=20, offset=0
    )
    assert result.total == 1
    assert result.items[0].full_name == "Alice Wanjiru"


async def test_pagination(business_ctx):
    service = CustomerService(business_ctx)
    for i in range(5):
        await service.create(CustomerCreateRequest(full_name=f"Customer {i}"))
    page1 = await service.search(
        search=None, status="ACTIVE", order="recent", limit=2, offset=0
    )
    page2 = await service.search(
        search=None, status="ACTIVE", order="recent", limit=2, offset=2
    )
    assert page1.total == 5
    assert len(page1.items) == 2
    assert len(page2.items) == 2
    assert {c.id for c in page1.items}.isdisjoint({c.id for c in page2.items})


async def test_filter_by_credit_level_and_blocked(business_ctx, session):
    service = CustomerService(business_ctx)
    normal = await service.create(CustomerCreateRequest(full_name="Normal Customer"))
    risky = await service.create(CustomerCreateRequest(full_name="Risky Customer"))
    blocked = await service.create(CustomerCreateRequest(full_name="Blocked Customer"))

    risky_row = await session.get(Customer, risky.id)
    risky_row.credit_level = "HIGH_RISK"
    blocked_row = await session.get(Customer, blocked.id)
    blocked_row.credit_level = "BLOCKED"
    blocked_row.is_credit_blocked = True
    await session.flush()
    await session.commit()

    high_risk = await service.search(
        search=None, status="ACTIVE", order="recent", limit=20, offset=0, credit_level="HIGH_RISK"
    )
    assert [c.full_name for c in high_risk.items] == ["Risky Customer"]

    blocked_only = await service.search(
        search=None, status="ACTIVE", order="recent", limit=20, offset=0, is_blocked=True
    )
    assert [c.full_name for c in blocked_only.items] == ["Blocked Customer"]


async def test_filter_has_active_debt_and_no_debt(business_ctx, session):
    service = CustomerService(business_ctx)
    in_debt = await service.create(CustomerCreateRequest(full_name="In Debt"))
    debt_free = await service.create(CustomerCreateRequest(full_name="Debt Free"))
    row = await session.get(Customer, in_debt.id)
    row.debt_balance = Decimal("15000")
    await session.flush()
    await session.commit()

    with_debt = await service.search(
        search=None, status="ACTIVE", order="recent", limit=20, offset=0, has_active_debt=True
    )
    assert [c.full_name for c in with_debt.items] == ["In Debt"]

    no_debt = await service.search(
        search=None, status="ACTIVE", order="recent", limit=20, offset=0, has_active_debt=False
    )
    assert {c.full_name for c in no_debt.items} == {"Debt Free"}


async def test_filter_debt_balance_range(business_ctx, session):
    service = CustomerService(business_ctx)
    low = await service.create(CustomerCreateRequest(full_name="Low Debt"))
    high = await service.create(CustomerCreateRequest(full_name="High Debt"))
    (await session.get(Customer, low.id)).debt_balance = Decimal("1000")
    (await session.get(Customer, high.id)).debt_balance = Decimal("100000")
    await session.flush()
    await session.commit()

    result = await service.search(
        search=None,
        status="ACTIVE",
        order="recent",
        limit=20,
        offset=0,
        min_debt_balance=Decimal("5000"),
    )
    assert [c.full_name for c in result.items] == ["High Debt"]


# --------------------------------------------------------------------------- #
# Customer Credibility Engine: credit profile, risk, reason
# --------------------------------------------------------------------------- #
async def test_credit_profile_unscored_customer(business_ctx):
    service = CustomerService(business_ctx)
    created = await service.create(CustomerCreateRequest(full_name="Brand New"))
    credit = await service.get_credit(created.id)
    assert credit.credit_level == "UNSCORED"
    assert credit.available_credit == Decimal("0")
    assert "No repayment history yet" in credit.risk_indicators
    assert credit.recommendation == "Not enough history yet — treat as a new customer."


async def test_credit_profile_high_risk_customer_has_risk_indicators(business_ctx, session):
    service = CustomerService(business_ctx)
    created = await service.create(CustomerCreateRequest(full_name="Risky One"))
    row = await session.get(Customer, created.id)
    row.credit_level = "HIGH_RISK"
    row.credit_score = 30
    row.debt_balance = Decimal("20000")
    row.recommended_credit_limit = Decimal("15000")
    row.late_payment_count = 2
    row.oldest_unpaid_at = datetime.now(timezone.utc) - timedelta(days=45)
    await session.flush()
    await session.commit()

    credit = await service.get_credit(created.id)
    assert credit.credit_level == "HIGH_RISK"
    assert "High risk credit level" in credit.risk_indicators
    assert "2 late payment(s) on record" in credit.risk_indicators
    assert any("days old" in r for r in credit.risk_indicators)
    assert "Debt balance has reached the recommended limit" in credit.risk_indicators
    assert credit.days_overdue >= 45


async def test_simulate_credit_within_limit_is_approved(business_ctx, session):
    service = CustomerService(business_ctx)
    created = await service.create(CustomerCreateRequest(full_name="Good Payer"))
    row = await session.get(Customer, created.id)
    row.credit_level = "GOOD"
    row.debt_balance = Decimal("5000")
    row.recommended_credit_limit = Decimal("50000")
    await session.flush()
    await session.commit()

    sim = await service.simulate_credit(created.id, Decimal("10000"))
    assert sim.would_be_approved is True
    assert sim.would_exceed_limit is False
    assert sim.projected_debt_balance == Decimal("15000")
    assert sim.remaining_available_credit == Decimal("45000")


async def test_simulate_credit_exceeding_limit_is_not_approved(business_ctx, session):
    service = CustomerService(business_ctx)
    created = await service.create(CustomerCreateRequest(full_name="Overextended"))
    row = await session.get(Customer, created.id)
    row.credit_level = "AVERAGE"
    row.debt_balance = Decimal("45000")
    row.recommended_credit_limit = Decimal("50000")
    await session.flush()
    await session.commit()

    sim = await service.simulate_credit(created.id, Decimal("10000"))
    assert sim.would_exceed_limit is True
    assert sim.would_be_approved is False
    assert "above the recommended limit" in sim.recommendation


async def test_simulate_credit_blocked_customer_never_approved(business_ctx, session):
    service = CustomerService(business_ctx)
    created = await service.create(CustomerCreateRequest(full_name="Blocked Payer"))
    row = await session.get(Customer, created.id)
    row.is_credit_blocked = True
    row.credit_level = "BLOCKED"
    await session.flush()
    await session.commit()

    sim = await service.simulate_credit(created.id, Decimal("1000"))
    assert sim.would_be_approved is False
    assert sim.is_credit_blocked is True
    assert sim.recommendation == "Avoid giving new debt."


# --------------------------------------------------------------------------- #
# Debt ledger
# --------------------------------------------------------------------------- #
async def test_get_ledger_returns_debts_for_customer(business_ctx, session):
    service = CustomerService(business_ctx)
    created = await service.create(CustomerCreateRequest(full_name="Ledger Test"))
    biz_id = business_ctx.business_id

    sale = Sale(
        business_id=biz_id,
        customer_id=created.id,
        payment_type_id=2,
        total_amount=Decimal("20000"),
        total_buying_cost=Decimal("12000"),
        outstanding_amount=Decimal("20000"),
        occurred_at=datetime.now(timezone.utc),
        receipt_number="RCP-TEST",
        is_correction=False,
    )
    session.add(sale)
    await session.flush()
    ledger = CustomerDebtLedger(
        business_id=biz_id,
        customer_id=created.id,
        sale_id=sale.id,
        original_amount=Decimal("20000"),
        paid_amount=Decimal("5000"),
        incurred_at=sale.occurred_at,
        status="PARTIAL",
    )
    session.add(ledger)
    await session.flush()
    await session.commit()

    result = await service.get_ledger(created.id, status=None, limit=10, offset=0)
    assert result.total == 1
    assert result.items[0].remaining_amount == Decimal("15000")
    assert result.items[0].status == "PARTIAL"


async def test_get_ledger_for_unknown_customer_raises(business_ctx):
    service = CustomerService(business_ctx)
    with pytest.raises(NotFoundError):
        await service.get_ledger(uuid.uuid4(), status=None, limit=10, offset=0)


# --------------------------------------------------------------------------- #
# Credit history (Decision Engine event timeline)
# --------------------------------------------------------------------------- #
async def test_credit_history_reads_decision_engine_issues(business_ctx, session):
    from app.models.issue import Issue, IssueType

    service = CustomerService(business_ctx)
    created = await service.create(CustomerCreateRequest(full_name="Event Customer"))
    biz_id = business_ctx.business_id

    itype = (
        await session.execute(
            __import__("sqlalchemy").select(IssueType).where(IssueType.code == "CUSTOMER_HIGH_RISK")
        )
    ).scalar_one()
    session.add(
        Issue(
            business_id=biz_id,
            issue_type_id=itype.id,
            ref_customer_id=created.id,
            severity="WARN",
            headline="Customer credit risk is high",
            data_snapshot={"score": 25, "level": "HIGH_RISK"},
            status="ACTIVE",
        )
    )
    await session.flush()
    await session.commit()

    history = await service.get_credit_history(customer_id=None, limit=10, offset=0)
    assert history.total == 1
    assert history.items[0].event_code == "CUSTOMER_HIGH_RISK"
    assert history.items[0].customer_name == "Event Customer"

    scoped = await service.get_credit_history(customer_id=created.id, limit=10, offset=0)
    assert scoped.total == 1
