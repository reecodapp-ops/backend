"""Expense module tests.

SQLite has no triggers, so these verify the service's own logic: validation,
unusual-amount soft warning, persistence, and history filtering. Trigger-driven
cash deduction is verified separately against real PostgreSQL.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from tests.conftest import setup_business

API = "/api/v1/expenses"
CAT_API = "/api/v1/expense-categories"
pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #
async def test_list_categories_includes_seeded(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.get(CAT_API, headers=headers)
    assert r.status_code == 200
    codes = [c["code"] for c in r.json()]
    # spot-check seeded codes from both groups
    assert "RENT" in codes
    assert "TRANSPORT" in codes
    assert "HOME" in codes
    assert "SAVINGS" in codes


# --------------------------------------------------------------------------- #
# Create — happy path
# --------------------------------------------------------------------------- #
async def test_create_expense_basic(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={"category_id": 2, "amount": "200000.00", "note": "monthly rent",
              "supplier_name": "Landlord"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["expense"]["amount"] == "200000.00"
    assert body["expense"]["category_id"] == 2
    assert body["expense"]["supplier_name"] == "Landlord"
    assert body["expense"]["is_correction"] is False
    # First expense for category -> no recent average yet, no warning
    assert body["warnings"] == []


async def test_create_expense_backdate(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={"category_id": 1, "amount": "5000", "occurred_at": "2026-03-15T08:00:00+00:00"},
        headers=headers,
    )
    assert r.status_code == 201
    assert r.json()["expense"]["occurred_at"].startswith("2026-03-15T08:00:00")


# --------------------------------------------------------------------------- #
# Validation failures
# --------------------------------------------------------------------------- #
async def test_zero_amount_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"category_id": 1, "amount": "0"}, headers=headers
    )
    assert r.status_code == 422


async def test_negative_amount_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"category_id": 1, "amount": "-100"}, headers=headers
    )
    assert r.status_code == 422


async def test_invalid_category_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"category_id": 9999, "amount": "1000"}, headers=headers
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_expense_category"


async def test_requires_business_header(client: AsyncClient):
    headers, _ = await setup_business(client)
    no_biz = {k: v for k, v in headers.items() if k != "X-Business-Id"}
    r = await client.post(
        API, json={"category_id": 1, "amount": "1000"}, headers=no_biz
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_business_id"


# --------------------------------------------------------------------------- #
# Unusual amount warning
# --------------------------------------------------------------------------- #
async def test_unusual_amount_warning(client: AsyncClient):
    headers, _ = await setup_business(client)
    # Build a baseline of similar small expenses, then a 5x outlier.
    for _ in range(4):
        await client.post(
            API, json={"category_id": 1, "amount": "10000"}, headers=headers
        )
    r = await client.post(
        API, json={"category_id": 1, "amount": "60000"}, headers=headers
    )
    assert r.status_code == 201
    assert any(w["code"] == "UNUSUAL_AMOUNT" for w in r.json()["warnings"])


async def test_no_unusual_warning_for_small_swing(client: AsyncClient):
    headers, _ = await setup_business(client)
    for _ in range(3):
        await client.post(
            API, json={"category_id": 1, "amount": "10000"}, headers=headers
        )
    # 1.5x avg < 2.5x threshold -> no warning
    r = await client.post(
        API, json={"category_id": 1, "amount": "15000"}, headers=headers
    )
    assert r.json()["warnings"] == []


# --------------------------------------------------------------------------- #
# History / filtering
# --------------------------------------------------------------------------- #
async def test_history_filter_by_category(client: AsyncClient):
    headers, _ = await setup_business(client)
    await client.post(API, json={"category_id": 1, "amount": "5000"}, headers=headers)
    await client.post(API, json={"category_id": 1, "amount": "6000"}, headers=headers)
    await client.post(API, json={"category_id": 2, "amount": "200000"}, headers=headers)
    r = await client.get(f"{API}?category_id=1", headers=headers)
    assert r.json()["total"] == 2
    r = await client.get(f"{API}?category_id=2", headers=headers)
    assert r.json()["total"] == 1


async def test_history_filter_by_group(client: AsyncClient):
    headers, _ = await setup_business(client)
    # group 1 = BUSINESS, group 2 = PERSONAL
    await client.post(API, json={"category_id": 1, "amount": "5000"}, headers=headers)
    await client.post(API, json={"category_id": 7, "amount": "100000"}, headers=headers)  # HOME
    await client.post(API, json={"category_id": 8, "amount": "300000"}, headers=headers)  # SCHOOL_FEES
    r = await client.get(f"{API}?group_id=1", headers=headers)
    assert r.json()["total"] == 1
    r = await client.get(f"{API}?group_id=2", headers=headers)
    assert r.json()["total"] == 2


async def test_history_scoped_to_business(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700900001")
    headers_b, _ = await setup_business(client, phone="+256700900002")
    await client.post(API, json={"category_id": 1, "amount": "1000"}, headers=headers_a)
    await client.post(API, json={"category_id": 1, "amount": "2000"}, headers=headers_b)
    a = await client.get(API, headers=headers_a)
    b = await client.get(API, headers=headers_b)
    assert a.json()["total"] == 1 and b.json()["total"] == 1
    assert a.json()["items"][0]["amount"] == "1000.00"
    assert b.json()["items"][0]["amount"] == "2000.00"
