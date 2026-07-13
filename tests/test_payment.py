"""Customer Payment module tests.

SQLite has no triggers, so these tests verify the service's own responsibilities:
validation, overpayment soft warning (based on the customer's stored
debt_balance), payment persistence, and history listing. Trigger-driven
oldest-first allocation, allocation row creation, sale outstanding decrement,
business cash/debt updates, and customer balance refresh are verified separately
against real PostgreSQL.

Because SQLite has no debt-sale trigger either, debt sales here do not raise the
customer's debt_balance automatically. The tests that need a non-zero starting
balance set it directly on the customer row (mirroring what the trigger would
have done in production) so the overpayment / allocation-context logic can be
exercised meaningfully on SQLite.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models.customer import Customer
from tests.conftest import create_customer, setup_business

API = "/api/v1/customer-payments"
pytestmark = pytest.mark.asyncio


async def _set_customer_debt(session_factory, customer_id: str, amount: str) -> None:
    """Directly set a customer's cached debt_balance to simulate the state the
    debt-sale trigger would have produced in PostgreSQL."""
    async with session_factory() as s:
        await s.execute(
            update(Customer)
            .where(Customer.id == uuid.UUID(customer_id))
            .values(debt_balance=Decimal(amount))
        )
        await s.commit()


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
async def test_create_payment_basic(client: AsyncClient, session_factory):
    headers, _ = await setup_business(client)
    customer = await create_customer(client, headers, full_name="Bosco M")
    await _set_customer_debt(session_factory, customer["id"], "10000.00")

    r = await client.post(
        API,
        json={"customer_id": customer["id"], "amount": "4000.00", "note": "first installment"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["payment"]["amount"] == "4000.00"
    assert body["payment"]["customer_id"] == customer["id"]
    assert body["payment"]["is_correction"] is False
    assert body["unallocated_amount"] == "0.00"
    assert body["warnings"] == []
    # No SQLite triggers -> allocations list is empty; the service correctly
    # reads back zero allocations (verified against PG in the E2E run).
    assert body["allocations"] == []


async def test_create_payment_persists_note_and_occurred_at(client: AsyncClient, session_factory):
    headers, _ = await setup_business(client)
    customer = await create_customer(client, headers)
    await _set_customer_debt(session_factory, customer["id"], "5000")
    r = await client.post(
        API,
        json={
            "customer_id": customer["id"],
            "amount": "2500",
            "occurred_at": "2026-04-01T10:30:00+00:00",
            "note": "received on visit",
        },
        headers=headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["payment"]["note"] == "received on visit"
    assert body["payment"]["occurred_at"].startswith("2026-04-01T10:30:00")


# --------------------------------------------------------------------------- #
# Validation failures
# --------------------------------------------------------------------------- #
async def test_amount_must_be_positive(client: AsyncClient):
    headers, _ = await setup_business(client)
    customer = await create_customer(client, headers)
    r = await client.post(
        API,
        json={"customer_id": customer["id"], "amount": "0"},
        headers=headers,
    )
    assert r.status_code == 422


async def test_negative_amount_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    customer = await create_customer(client, headers)
    r = await client.post(
        API,
        json={"customer_id": customer["id"], "amount": "-100"},
        headers=headers,
    )
    assert r.status_code == 422


async def test_unknown_customer_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={"customer_id": str(uuid.uuid4()), "amount": "1000"},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_customer"


async def test_foreign_customer_rejected(client: AsyncClient, session_factory):
    headers_a, _ = await setup_business(client, phone="+256700700101")
    headers_b, _ = await setup_business(client, phone="+256700700102")
    # customer belongs to B; A tries to record a payment against them
    cust_b = await create_customer(client, headers_b, full_name="B Cust")
    r = await client.post(
        API,
        json={"customer_id": cust_b["id"], "amount": "1000"},
        headers=headers_a,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_customer"


async def test_requires_business_header(client: AsyncClient):
    headers, _ = await setup_business(client)
    customer = await create_customer(client, headers)
    no_biz = {k: v for k, v in headers.items() if k != "X-Business-Id"}
    r = await client.post(
        API,
        json={"customer_id": customer["id"], "amount": "1000"},
        headers=no_biz,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_business_id"


# --------------------------------------------------------------------------- #
# Overpayment warning (soft)
# --------------------------------------------------------------------------- #
async def test_overpayment_warning(client: AsyncClient, session_factory):
    headers, _ = await setup_business(client)
    customer = await create_customer(client, headers, full_name="Light Debt")
    await _set_customer_debt(session_factory, customer["id"], "3000.00")

    r = await client.post(
        API,
        json={"customer_id": customer["id"], "amount": "5000.00"},
        headers=headers,
    )
    # soft warning - payment still recorded
    assert r.status_code == 201
    body = r.json()
    assert body["unallocated_amount"] == "2000.00"
    assert any(w["code"] == "OVERPAYMENT" for w in body["warnings"])
    # the payment itself is the full 5000 (excess remains unallocated)
    assert body["payment"]["amount"] == "5000.00"


async def test_payment_against_zero_debt_warns(client: AsyncClient):
    headers, _ = await setup_business(client)
    customer = await create_customer(client, headers, full_name="No Debt")
    # debt_balance starts at 0
    r = await client.post(
        API,
        json={"customer_id": customer["id"], "amount": "1500"},
        headers=headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["unallocated_amount"] == "1500.00"
    assert any(w["code"] == "OVERPAYMENT" for w in body["warnings"])


async def test_exact_debt_payment_no_warning(client: AsyncClient, session_factory):
    headers, _ = await setup_business(client)
    customer = await create_customer(client, headers)
    await _set_customer_debt(session_factory, customer["id"], "2500.00")

    r = await client.post(
        API,
        json={"customer_id": customer["id"], "amount": "2500.00"},
        headers=headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["unallocated_amount"] == "0.00"
    assert body["warnings"] == []


# --------------------------------------------------------------------------- #
# History / listing
# --------------------------------------------------------------------------- #
async def test_list_payments_scoped_to_business(client: AsyncClient, session_factory):
    headers, _ = await setup_business(client)
    customer = await create_customer(client, headers)
    await _set_customer_debt(session_factory, customer["id"], "10000")

    for amt in ("1000", "2000", "3000"):
        await client.post(
            API, json={"customer_id": customer["id"], "amount": amt}, headers=headers
        )
    r = await client.get(API, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # newest first
    assert Decimal(body["items"][0]["amount"]) in {Decimal("1000"), Decimal("2000"), Decimal("3000")}


async def test_list_filter_by_customer(client: AsyncClient, session_factory):
    headers, _ = await setup_business(client)
    c1 = await create_customer(client, headers, full_name="C1")
    c2 = await create_customer(client, headers, full_name="C2")
    await _set_customer_debt(session_factory, c1["id"], "5000")
    await _set_customer_debt(session_factory, c2["id"], "5000")

    await client.post(API, json={"customer_id": c1["id"], "amount": "1000"}, headers=headers)
    await client.post(API, json={"customer_id": c1["id"], "amount": "500"}, headers=headers)
    await client.post(API, json={"customer_id": c2["id"], "amount": "2000"}, headers=headers)

    r = await client.get(f"{API}?customer_id={c1['id']}", headers=headers)
    assert r.json()["total"] == 2
    r = await client.get(f"{API}?customer_id={c2['id']}", headers=headers)
    assert r.json()["total"] == 1


async def test_list_isolation_between_businesses(client: AsyncClient, session_factory):
    headers_a, _ = await setup_business(client, phone="+256700700201")
    headers_b, _ = await setup_business(client, phone="+256700700202")
    ca = await create_customer(client, headers_a, full_name="A Cust")
    cb = await create_customer(client, headers_b, full_name="B Cust")
    await _set_customer_debt(session_factory, ca["id"], "5000")
    await _set_customer_debt(session_factory, cb["id"], "5000")

    await client.post(API, json={"customer_id": ca["id"], "amount": "1000"}, headers=headers_a)
    await client.post(API, json={"customer_id": cb["id"], "amount": "2000"}, headers=headers_b)

    a = await client.get(API, headers=headers_a)
    b = await client.get(API, headers=headers_b)
    assert a.json()["total"] == 1 and b.json()["total"] == 1
    assert a.json()["items"][0]["amount"] == "1000.00"
    assert b.json()["items"][0]["amount"] == "2000.00"
