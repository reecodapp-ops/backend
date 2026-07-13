"""Correction module tests.

Tests on SQLite verify the SERVICE workflow: original-record validation, flipping
the ``is_correction`` flag, inserting the replacement, writing the audit row,
and handling unsupported/invalid types. Cash and debt reconciliation are
verified separately against real PostgreSQL (only there does
``reconcile_business_cash`` exist).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.expense import Expense
from app.models.payment import CustomerPayment
from tests.conftest import create_customer, setup_business

API = "/api/v1/corrections"
pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# EXPENSE_EDIT happy path
# --------------------------------------------------------------------------- #
async def test_expense_edit_workflow(client: AsyncClient, session_factory):
    headers, _ = await setup_business(client)
    # original expense
    exp = (
        await client.post(
            "/api/v1/expenses",
            json={"category_id": 1, "amount": "50000", "note": "transport"},
            headers=headers,
        )
    ).json()["expense"]
    original_id = exp["id"]

    r = await client.post(
        API,
        json={
            "correction_type_code": "EXPENSE_EDIT",
            "original_record_id": original_id,
            "reason": "Typo: meant 5000",
            "new_values": {"amount": "5000"},
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["correction"]["correction_type_code"] == "EXPENSE_EDIT"
    assert body["correction"]["field_changed"] == "amount"
    assert body["correction"]["old_value_text"] == "50000.00"
    assert body["correction"]["new_value_text"] == "5000"
    assert body["correction"]["original_record_id"] == original_id
    assert body["corrected_record_id"] != original_id

    # original is flipped to is_correction = TRUE so reconcile excludes it
    async with session_factory() as s:
        rows = (await s.execute(select(Expense).order_by(Expense.created_at.asc()))).scalars().all()
    by_id = {str(r.id): r for r in rows}
    assert by_id[original_id].is_correction is True
    assert by_id[body["corrected_record_id"]].is_correction is False
    # replacement has original_expense_id pointing back
    assert str(by_id[body["corrected_record_id"]].original_expense_id) == original_id


async def test_correction_appears_in_history(client: AsyncClient):
    headers, _ = await setup_business(client)
    exp = (
        await client.post(
            "/api/v1/expenses",
            json={"category_id": 1, "amount": "5000"},
            headers=headers,
        )
    ).json()["expense"]
    await client.post(
        API,
        json={
            "correction_type_code": "EXPENSE_EDIT",
            "original_record_id": exp["id"],
            "reason": "wrong amount",
            "new_values": {"amount": "500"},
        },
        headers=headers,
    )
    r = await client.get(API, headers=headers)
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["correction_type_code"] == "EXPENSE_EDIT"


# --------------------------------------------------------------------------- #
# PAYMENT_EDIT happy path
# --------------------------------------------------------------------------- #
async def test_payment_edit_workflow(client: AsyncClient, session_factory):
    headers, _ = await setup_business(client)
    customer = await create_customer(client, headers)
    # set debt so the payment is sensible
    async with session_factory() as s:
        from app.models.customer import Customer

        await s.execute(
            Customer.__table__.update()
            .where(Customer.id == uuid.UUID(customer["id"]))
            .values(debt_balance=Decimal("10000"))
        )
        await s.commit()
    pay = (
        await client.post(
            "/api/v1/customer-payments",
            json={"customer_id": customer["id"], "amount": "3000"},
            headers=headers,
        )
    ).json()["payment"]
    r = await client.post(
        API,
        json={
            "correction_type_code": "PAYMENT_EDIT",
            "original_record_id": pay["id"],
            "reason": "received 5000, not 3000",
            "new_values": {"amount": "5000"},
        },
        headers=headers,
    )
    assert r.status_code == 201
    assert r.json()["correction"]["field_changed"] == "amount"
    async with session_factory() as s:
        rows = (await s.execute(select(CustomerPayment).order_by(CustomerPayment.created_at.asc()))).scalars().all()
    assert any(p.is_correction for p in rows)
    assert any(not p.is_correction and p.amount == Decimal("5000") for p in rows)


# --------------------------------------------------------------------------- #
# CAPITAL_EDIT
# --------------------------------------------------------------------------- #
async def test_capital_edit_workflow(client: AsyncClient):
    headers, _ = await setup_business(client)
    txn = (
        await client.post(
            "/api/v1/capital-transactions",
            json={"type_code": "MONEY_IN", "amount": "100000", "source_type_id": 1},
            headers=headers,
        )
    ).json()["capital_transaction"]
    r = await client.post(
        API,
        json={
            "correction_type_code": "CAPITAL_EDIT",
            "original_record_id": txn["id"],
            "reason": "amount was actually 75000",
            "new_values": {"amount": "75000"},
        },
        headers=headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["correction"]["old_value_text"] == "100000.00"
    assert body["correction"]["new_value_text"] == "75000"


# --------------------------------------------------------------------------- #
# SALE_EDIT — cash only here
# --------------------------------------------------------------------------- #
async def test_sale_edit_cash_only(client: AsyncClient):
    headers, _ = await setup_business(client)
    from tests.conftest import create_product

    product = await create_product(client, headers, name="Sugar", stock_qty="50")
    sale = (
        await client.post(
            "/api/v1/sales",
            json={
                "payment_type": "CASH",
                "items": [{"product_id": product["id"], "product_name": "Sugar",
                           "quantity": "1", "unit_price": "4000"}],
            },
            headers=headers,
        )
    ).json()["sale"]
    r = await client.post(
        API,
        json={
            "correction_type_code": "SALE_EDIT",
            "original_record_id": sale["id"],
            "reason": "rang up wrong price",
            "new_values": {"total_amount": "3500"},
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["correction"]["field_changed"] == "total_amount"


async def test_sale_edit_debt_sale_unsupported(client: AsyncClient, session_factory):
    headers, _ = await setup_business(client)
    from tests.conftest import create_product

    product = await create_product(client, headers, name="Sugar", stock_qty="50")
    customer = await create_customer(client, headers)
    sale = (
        await client.post(
            "/api/v1/sales",
            json={
                "payment_type": "ON_DEBT",
                "customer_id": customer["id"],
                "items": [{"product_id": product["id"], "product_name": "Sugar",
                           "quantity": "1", "unit_price": "4000"}],
            },
            headers=headers,
        )
    ).json()["sale"]
    r = await client.post(
        API,
        json={
            "correction_type_code": "SALE_EDIT",
            "original_record_id": sale["id"],
            "reason": "wrong amount",
            "new_values": {"total_amount": "3500"},
        },
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "debt_sale_correction_unsupported"


# --------------------------------------------------------------------------- #
# Validation failures
# --------------------------------------------------------------------------- #
async def test_unsupported_correction_type(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={
            "correction_type_code": "STOCK_EDIT",
            "original_record_id": str(uuid.uuid4()),
            "reason": "x",
            "new_values": {"amount": "1000"},
        },
        headers=headers,
    )
    # STOCK_EDIT is not in the Literal allowed set -> 422 at Pydantic layer
    assert r.status_code == 422


async def test_missing_new_amount(client: AsyncClient):
    headers, _ = await setup_business(client)
    exp = (
        await client.post(
            "/api/v1/expenses",
            json={"category_id": 1, "amount": "5000"},
            headers=headers,
        )
    ).json()["expense"]
    r = await client.post(
        API,
        json={
            "correction_type_code": "EXPENSE_EDIT",
            "original_record_id": exp["id"],
            "reason": "x",
            "new_values": {"note": "no amount given"},
        },
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_new_amount"


async def test_unknown_original_record(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={
            "correction_type_code": "EXPENSE_EDIT",
            "original_record_id": str(uuid.uuid4()),
            "reason": "x",
            "new_values": {"amount": "100"},
        },
        headers=headers,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "original_record_not_found"


async def test_already_corrected_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    exp = (
        await client.post(
            "/api/v1/expenses",
            json={"category_id": 1, "amount": "5000"},
            headers=headers,
        )
    ).json()["expense"]
    # first correction succeeds
    r1 = await client.post(
        API,
        json={
            "correction_type_code": "EXPENSE_EDIT",
            "original_record_id": exp["id"],
            "reason": "first",
            "new_values": {"amount": "500"},
        },
        headers=headers,
    )
    assert r1.status_code == 201
    # second correction on same original is rejected
    r2 = await client.post(
        API,
        json={
            "correction_type_code": "EXPENSE_EDIT",
            "original_record_id": exp["id"],
            "reason": "second",
            "new_values": {"amount": "300"},
        },
        headers=headers,
    )
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "already_corrected"


async def test_correction_isolated_between_businesses(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256712200001")
    headers_b, _ = await setup_business(client, phone="+256712200002")
    exp_a = (
        await client.post(
            "/api/v1/expenses",
            json={"category_id": 1, "amount": "5000"},
            headers=headers_a,
        )
    ).json()["expense"]
    # business B tries to correct A's expense
    r = await client.post(
        API,
        json={
            "correction_type_code": "EXPENSE_EDIT",
            "original_record_id": exp_a["id"],
            "reason": "wrong",
            "new_values": {"amount": "100"},
        },
        headers=headers_b,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "original_record_not_found"
