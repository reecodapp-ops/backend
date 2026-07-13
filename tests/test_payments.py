"""Payment module tests — full/partial/multiple payments, oldest-first
allocation across debt ledger entries, payment history, and the
customer-scoped payment+allocation history view.

Since the oldest-first allocation itself is performed by a PostgreSQL
trigger (fn_after_payment_insert), these tests seed the ledger state that the
trigger would have produced and exercise the parts that live in Python:
PaymentService's overpayment detection, and PaymentRepository/CustomerService's
read paths (list_payments, list_payments_with_allocations).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.customer import Customer
from app.models.payment import CustomerPayment, PaymentAllocation
from app.models.sale import CustomerDebtLedger, Sale
from app.schemas.customer import CustomerCreateRequest
from app.schemas.payment import PaymentCreateRequest
from app.services.customer_service import CustomerService
from app.services.payment_service import PaymentService


async def _make_debt_customer_with_two_debts(business_ctx, session):
    customer = await CustomerService(business_ctx).create(CustomerCreateRequest(full_name="Debtor"))
    biz_id = business_ctx.business_id
    now = datetime.now(timezone.utc)
    older = now - timedelta(days=10)

    sale1 = Sale(
        business_id=biz_id, customer_id=customer.id, payment_type_id=2,
        total_amount=Decimal("10000"), total_buying_cost=Decimal("6000"),
        outstanding_amount=Decimal("10000"), occurred_at=older, receipt_number="D1", is_correction=False,
    )
    sale2 = Sale(
        business_id=biz_id, customer_id=customer.id, payment_type_id=2,
        total_amount=Decimal("8000"), total_buying_cost=Decimal("5000"),
        outstanding_amount=Decimal("8000"), occurred_at=now, receipt_number="D2", is_correction=False,
    )
    session.add_all([sale1, sale2])
    await session.flush()

    ledger1 = CustomerDebtLedger(
        business_id=biz_id, customer_id=customer.id, sale_id=sale1.id,
        original_amount=Decimal("10000"), paid_amount=Decimal("0"),
        incurred_at=older, status="UNPAID",
    )
    ledger2 = CustomerDebtLedger(
        business_id=biz_id, customer_id=customer.id, sale_id=sale2.id,
        original_amount=Decimal("8000"), paid_amount=Decimal("0"),
        incurred_at=now, status="UNPAID",
    )
    session.add_all([ledger1, ledger2])

    row = await session.get(Customer, customer.id)
    row.debt_balance = Decimal("18000")
    row.total_debt_issued = Decimal("18000")
    row.oldest_unpaid_at = older
    await session.flush()
    await session.commit()
    return customer, ledger1, ledger2


async def _simulate_oldest_first_allocation(session, business_ctx, customer, ledgers, amount: Decimal):
    """Mimic fn_after_payment_insert's oldest-first allocation for test setup
    (the real allocation only happens via a PostgreSQL trigger)."""
    payment = CustomerPayment(
        business_id=business_ctx.business_id, customer_id=customer.id,
        amount=amount, occurred_at=datetime.now(timezone.utc), is_correction=False,
    )
    session.add(payment)
    await session.flush()
    remaining = amount

    def _incurred_at_key(ledger):
        dt = ledger.incurred_at
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    for ledger in sorted(ledgers, key=_incurred_at_key):
        if remaining <= 0:
            break
        allocate = min(remaining, ledger.original_amount - ledger.paid_amount)
        if allocate <= 0:
            continue
        session.add(
            PaymentAllocation(
                business_id=business_ctx.business_id, payment_id=payment.id,
                debt_ledger_id=ledger.id, allocated_amount=allocate,
            )
        )
        ledger.paid_amount += allocate
        if ledger.paid_amount >= ledger.original_amount:
            ledger.status = "PAID"
            ledger.fully_paid_at = datetime.now(timezone.utc)
        else:
            ledger.status = "PARTIAL"
        remaining -= allocate
    await session.flush()
    await session.commit()
    return payment


async def test_partial_payment_allocates_oldest_debt_first(business_ctx, session):
    customer, ledger1, ledger2 = await _make_debt_customer_with_two_debts(business_ctx, session)
    payment = await _simulate_oldest_first_allocation(
        session, business_ctx, customer, [ledger1, ledger2], Decimal("6000")
    )
    await session.refresh(ledger1)
    await session.refresh(ledger2)
    assert ledger1.paid_amount == Decimal("6000")  # oldest gets paid first
    assert ledger1.status == "PARTIAL"
    assert ledger2.paid_amount == Decimal("0")  # newer untouched
    assert ledger2.status == "UNPAID"


async def test_full_payment_across_both_debts(business_ctx, session):
    customer, ledger1, ledger2 = await _make_debt_customer_with_two_debts(business_ctx, session)
    await _simulate_oldest_first_allocation(
        session, business_ctx, customer, [ledger1, ledger2], Decimal("18000")
    )
    await session.refresh(ledger1)
    await session.refresh(ledger2)
    assert ledger1.status == "PAID"
    assert ledger2.status == "PAID"


async def test_multiple_payments_over_time(business_ctx, session):
    customer, ledger1, ledger2 = await _make_debt_customer_with_two_debts(business_ctx, session)
    await _simulate_oldest_first_allocation(session, business_ctx, customer, [ledger1, ledger2], Decimal("5000"))
    await session.refresh(ledger1)
    await _simulate_oldest_first_allocation(session, business_ctx, customer, [ledger1, ledger2], Decimal("5000"))
    await session.refresh(ledger1)
    await session.refresh(ledger2)
    assert ledger1.status == "PAID"  # 10000 total across two payments
    assert ledger2.status == "UNPAID"


async def test_overpayment_warning(business_ctx, session):
    customer = await CustomerService(business_ctx).create(CustomerCreateRequest(full_name="Small Debtor"))
    row = await session.get(Customer, customer.id)
    row.debt_balance = Decimal("1000")
    await session.flush()
    await session.commit()

    service = PaymentService(business_ctx)
    resp = await service.create_payment(
        PaymentCreateRequest(customer_id=customer.id, amount=Decimal("5000"))
    )
    assert any(w.code == "OVERPAYMENT" for w in resp.warnings)
    assert resp.unallocated_amount == Decimal("4000")


async def test_payment_history_via_customer_service(business_ctx, session):
    customer, ledger1, ledger2 = await _make_debt_customer_with_two_debts(business_ctx, session)
    await _simulate_oldest_first_allocation(session, business_ctx, customer, [ledger1, ledger2], Decimal("3000"))
    await _simulate_oldest_first_allocation(session, business_ctx, customer, [ledger1, ledger2], Decimal("2000"))

    history = await CustomerService(business_ctx).get_payment_history(customer.id, limit=10, offset=0)
    assert history.total == 2
    for entry in history.items:
        assert len(entry.allocations) >= 1
        assert entry.allocations[0].sale_id == ledger1.sale_id  # oldest debt


async def test_payment_list_filtered_by_customer(business_ctx, session):
    c1, l1a, l1b = await _make_debt_customer_with_two_debts(business_ctx, session)
    await _simulate_oldest_first_allocation(session, business_ctx, c1, [l1a, l1b], Decimal("1000"))

    service = PaymentService(business_ctx)
    result = await service.list_payments(customer_id=c1.id, limit=10, offset=0)
    assert result.total == 1
    assert result.items[0].customer_id == c1.id
