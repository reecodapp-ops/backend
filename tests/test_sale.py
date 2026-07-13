"""Sales module tests.

These run on SQLite, which has no triggers, so they assert the parts the SERVICE
is responsible for: validation, totals, buying-cost/name snapshots,
outstanding_amount, receipt-number format, and soft warnings. Trigger-driven
effects (cash/debt/stock mutation, debt-ledger creation) are verified separately
against real PostgreSQL in the end-to-end check.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from tests.conftest import create_customer, create_product, setup_business

API = "/api/v1/sales"
pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Cash sale — happy path
# --------------------------------------------------------------------------- #
async def test_cash_sale_basic(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers, name="Sugar 1kg",
                                   selling_price="4000", buying_price="3200", stock_qty="50")
    r = await client.post(
        API,
        json={
            "payment_type": "CASH",
            "items": [
                {"product_id": product["id"], "product_name": "Sugar 1kg",
                 "quantity": "3", "unit_price": "4000"}
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    sale = body["sale"]
    assert sale["total_amount"] == "12000.00"
    assert sale["total_buying_cost"] == "9600.00"  # 3 * 3200
    assert sale["gross_margin"] == "2400.00"
    # cash sale has no outstanding amount
    assert sale["outstanding_amount"] == "0.00"
    assert sale["customer_id"] is None
    assert sale["receipt_number"].startswith("RCP-")
    assert len(sale["items"]) == 1
    item = sale["items"][0]
    assert item["buying_cost"] == "3200.00"  # snapshot
    assert item["line_total"] == "12000.00"
    assert body["warnings"] == []


async def test_cash_sale_multiple_items_totals(client: AsyncClient):
    headers, _ = await setup_business(client)
    p1 = await create_product(client, headers, name="Rice 1kg",
                              selling_price="4500", buying_price="3500", stock_qty="100")
    p2 = await create_product(client, headers, name="Salt 1kg",
                              selling_price="1500", buying_price="1000", stock_qty="100")
    r = await client.post(
        API,
        json={
            "payment_type": "CASH",
            "items": [
                {"product_id": p1["id"], "product_name": "Rice 1kg", "quantity": "2", "unit_price": "4500"},
                {"product_id": p2["id"], "product_name": "Salt 1kg", "quantity": "5", "unit_price": "1500"},
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201
    sale = r.json()["sale"]
    # 2*4500 + 5*1500 = 9000 + 7500 = 16500
    assert sale["total_amount"] == "16500.00"
    # buy: 2*3500 + 5*1000 = 7000 + 5000 = 12000
    assert sale["total_buying_cost"] == "12000.00"
    assert len(sale["items"]) == 2


async def test_cash_sale_adhoc_item_no_product_id(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={
            "payment_type": "CASH",
            "items": [
                {"product_id": None, "product_name": "Loose sweets", "quantity": "10", "unit_price": "200"}
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201
    sale = r.json()["sale"]
    assert sale["total_amount"] == "2000.00"
    # no product link -> no buying cost snapshot
    assert sale["items"][0]["buying_cost"] is None
    assert sale["items"][0]["product_name_snapshot"] == "Loose sweets"


# --------------------------------------------------------------------------- #
# Debt sale — happy path
# --------------------------------------------------------------------------- #
async def test_debt_sale_sets_outstanding_and_customer(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers, name="Cooking Oil 1L",
                                   selling_price="8000", buying_price="6500", stock_qty="30")
    customer = await create_customer(client, headers, full_name="Janet Wekesa")
    r = await client.post(
        API,
        json={
            "payment_type": "ON_DEBT",
            "customer_id": customer["id"],
            "items": [
                {"product_id": product["id"], "product_name": "Cooking Oil 1L",
                 "quantity": "2", "unit_price": "8000"}
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    sale = r.json()["sale"]
    assert sale["total_amount"] == "16000.00"
    # debt sale: full amount outstanding
    assert sale["outstanding_amount"] == "16000.00"
    assert sale["customer_id"] == customer["id"]
    assert sale["payment_type_id"] == 2


# --------------------------------------------------------------------------- #
# Validation failures
# --------------------------------------------------------------------------- #
async def test_empty_items_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(API, json={"payment_type": "CASH", "items": []}, headers=headers)
    assert r.status_code == 422


async def test_zero_quantity_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers)
    r = await client.post(
        API,
        json={"payment_type": "CASH",
              "items": [{"product_id": product["id"], "product_name": "Sugar 1kg",
                         "quantity": "0", "unit_price": "4000"}]},
        headers=headers,
    )
    assert r.status_code == 422


async def test_negative_price_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={"payment_type": "CASH",
              "items": [{"product_id": None, "product_name": "X",
                         "quantity": "1", "unit_price": "-5"}]},
        headers=headers,
    )
    assert r.status_code == 422


async def test_debt_sale_without_customer_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers)
    r = await client.post(
        API,
        json={"payment_type": "ON_DEBT",
              "items": [{"product_id": product["id"], "product_name": "Sugar 1kg",
                         "quantity": "1", "unit_price": "4000"}]},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "customer_required_for_debt"


async def test_debt_sale_foreign_customer_rejected(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700000101")
    headers_b, _ = await setup_business(client, phone="+256700000102")
    # customer belongs to business B
    cust_b = await create_customer(client, headers_b, full_name="B Customer")
    product_a = await create_product(client, headers_a)
    # business A tries to sell on debt to B's customer
    r = await client.post(
        API,
        json={"payment_type": "ON_DEBT", "customer_id": cust_b["id"],
              "items": [{"product_id": product_a["id"], "product_name": "Sugar 1kg",
                         "quantity": "1", "unit_price": "4000"}]},
        headers=headers_a,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_customer"


async def test_foreign_product_rejected(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700000201")
    headers_b, _ = await setup_business(client, phone="+256700000202")
    product_b = await create_product(client, headers_b, name="B Product")
    # business A tries to sell B's product
    r = await client.post(
        API,
        json={"payment_type": "CASH",
              "items": [{"product_id": product_b["id"], "product_name": "B Product",
                         "quantity": "1", "unit_price": "1000"}]},
        headers=headers_a,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_product"


async def test_requires_business_header(client: AsyncClient):
    headers, _ = await setup_business(client)
    no_biz = {k: v for k, v in headers.items() if k != "X-Business-Id"}
    r = await client.post(
        API,
        json={"payment_type": "CASH",
              "items": [{"product_id": None, "product_name": "X", "quantity": "1", "unit_price": "100"}]},
        headers=no_biz,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_business_id"


# --------------------------------------------------------------------------- #
# Warnings (soft, non-blocking)
# --------------------------------------------------------------------------- #
async def test_oversell_warning(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers, name="Limited Item", stock_qty="5")
    r = await client.post(
        API,
        json={"payment_type": "CASH",
              "items": [{"product_id": product["id"], "product_name": "Limited Item",
                         "quantity": "8", "unit_price": "1000"}]},
        headers=headers,
    )
    # still succeeds (soft warning)
    assert r.status_code == 201
    warnings = r.json()["warnings"]
    assert any(w["code"] == "OVERSELL" for w in warnings)


async def test_duplicate_product_warning(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers, name="Repeated", stock_qty="100")
    r = await client.post(
        API,
        json={"payment_type": "CASH",
              "items": [
                  {"product_id": product["id"], "product_name": "Repeated", "quantity": "1", "unit_price": "1000"},
                  {"product_id": product["id"], "product_name": "Repeated", "quantity": "2", "unit_price": "1000"},
              ]},
        headers=headers,
    )
    assert r.status_code == 201
    warnings = r.json()["warnings"]
    assert any(w["code"] == "DUPLICATE_PRODUCT" for w in warnings)
    # both lines still recorded, total includes both
    assert r.json()["sale"]["total_amount"] == "3000.00"


# --------------------------------------------------------------------------- #
# Receipt numbering
# --------------------------------------------------------------------------- #
async def test_receipt_number_format_and_increment(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers, stock_qty="100")
    item = {"product_id": product["id"], "product_name": "Sugar 1kg", "quantity": "1", "unit_price": "4000"}

    r1 = await client.post(API, json={"payment_type": "CASH", "items": [item]}, headers=headers)
    r2 = await client.post(API, json={"payment_type": "CASH", "items": [item]}, headers=headers)
    rn1 = r1.json()["sale"]["receipt_number"]
    rn2 = r2.json()["sale"]["receipt_number"]

    import re
    assert re.match(r"^RCP-\d{8}-\d{4}$", rn1)
    assert re.match(r"^RCP-\d{8}-\d{4}$", rn2)
    # second receipt increments
    seq1 = int(rn1.split("-")[-1])
    seq2 = int(rn2.split("-")[-1])
    assert seq2 == seq1 + 1


async def test_receipt_numbers_isolated_per_business(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700000301")
    headers_b, _ = await setup_business(client, phone="+256700000302")
    pa = await create_product(client, headers_a, stock_qty="50")
    pb = await create_product(client, headers_b, stock_qty="50")

    ra = await client.post(API, json={"payment_type": "CASH",
        "items": [{"product_id": pa["id"], "product_name": "Sugar 1kg", "quantity": "1", "unit_price": "4000"}]},
        headers=headers_a)
    rb = await client.post(API, json={"payment_type": "CASH",
        "items": [{"product_id": pb["id"], "product_name": "Sugar 1kg", "quantity": "1", "unit_price": "4000"}]},
        headers=headers_b)
    # each business starts its own daily sequence at 0001
    assert ra.json()["sale"]["receipt_number"].endswith("-0001")
    assert rb.json()["sale"]["receipt_number"].endswith("-0001")
