"""Stock Purchase module tests.

Service-layer tests on SQLite (no triggers). The trigger-driven cash decrement
and stock increment are verified end-to-end against real PostgreSQL.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient

from tests.conftest import create_product, setup_business

API = "/api/v1/stock-purchases"
pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
async def test_create_purchase_basic(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(
        client, headers, name="Sugar 1kg", buying_price="3200", stock_qty="20"
    )
    r = await client.post(
        API,
        json={
            "supplier_name_free": "Mukwano Distributors",
            "items": [
                {"product_id": product["id"], "product_name": "Sugar 1kg",
                 "quantity": "30", "total_item_cost": "96000"}
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    p = body["purchase"]
    assert p["total_buying_cost"] == "96000.00"
    assert p["supplier_name_free"] == "Mukwano Distributors"
    assert p["supplier_id"] is None
    assert p["is_correction"] is False
    assert len(p["items"]) == 1
    item = p["items"][0]
    # unit cost computed from total / qty
    assert item["unit_buying_cost"] == "3200.00"
    assert item["total_item_cost"] == "96000.00"
    assert body["warnings"] == []


async def test_create_purchase_multi_items_totals(client: AsyncClient):
    headers, _ = await setup_business(client)
    p1 = await create_product(client, headers, name="Rice", buying_price="3500", stock_qty="0")
    p2 = await create_product(client, headers, name="Salt", buying_price="1000", stock_qty="0")
    r = await client.post(
        API,
        json={
            "items": [
                {"product_id": p1["id"], "product_name": "Rice", "quantity": "10", "total_item_cost": "35000"},
                {"product_id": p2["id"], "product_name": "Salt", "quantity": "20", "total_item_cost": "20000"},
            ]
        },
        headers=headers,
    )
    assert r.status_code == 201
    assert r.json()["purchase"]["total_buying_cost"] == "55000.00"
    assert len(r.json()["purchase"]["items"]) == 2


async def test_create_purchase_adhoc_item(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={
            "items": [
                {"product_id": None, "product_name": "Misc packaging",
                 "quantity": "100", "total_item_cost": "5000"}
            ]
        },
        headers=headers,
    )
    assert r.status_code == 201
    item = r.json()["purchase"]["items"][0]
    assert item["product_id"] is None
    assert item["product_name_snapshot"] == "Misc packaging"


async def test_avg_buying_cost_recomputed_on_purchase(client: AsyncClient):
    """Service owns avg_buying_cost (no trigger maintains it). On SQLite this
    still runs because the stock-increment trigger is absent — we test the
    weighted-average calculation against current stock_on_hand exactly as the
    service would in production after the trigger has updated it."""
    headers, _ = await setup_business(client)
    # On SQLite the item trigger does not fire, so stock_on_hand stays at its
    # initial value. The service's weighted-avg formula uses current stock; with
    # zero starting stock the new avg equals the unit cost of the purchase.
    product = await create_product(
        client, headers, name="Tea Leaves", buying_price="3800", stock_qty="0"
    )
    r = await client.post(
        API,
        json={"items": [{"product_id": product["id"], "product_name": "Tea Leaves",
                         "quantity": "20", "total_item_cost": "80000"}]},
        headers=headers,
    )
    assert r.status_code == 201
    # fetch product
    pr = (await client.get(f"/api/v1/products/{product['id']}", headers=headers)).json()
    # SQLite: trigger didn't bump stock; the weighted-avg branch for stock<=0
    # uses purchased cost / purchased qty = 80000 / 20 = 4000.
    assert pr["avg_buying_cost"] == "4000.00"
    # buying_price also bumped to the latest unit cost
    assert pr["buying_price"] == "4000.00"


# --------------------------------------------------------------------------- #
# Validation failures
# --------------------------------------------------------------------------- #
async def test_empty_items_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(API, json={"items": []}, headers=headers)
    assert r.status_code == 422


async def test_zero_quantity_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={"items": [{"product_id": None, "product_name": "X",
                         "quantity": "0", "total_item_cost": "100"}]},
        headers=headers,
    )
    assert r.status_code == 422


async def test_negative_cost_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={"items": [{"product_id": None, "product_name": "X",
                         "quantity": "1", "total_item_cost": "-50"}]},
        headers=headers,
    )
    assert r.status_code == 422


async def test_foreign_product_rejected(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700910001")
    headers_b, _ = await setup_business(client, phone="+256700910002")
    product_b = await create_product(client, headers_b, name="B Product", stock_qty="0")
    r = await client.post(
        API,
        json={"items": [{"product_id": product_b["id"], "product_name": "B Product",
                         "quantity": "5", "total_item_cost": "5000"}]},
        headers=headers_a,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_product"


async def test_foreign_supplier_rejected(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700920001")
    headers_b, _ = await setup_business(client, phone="+256700920002")
    sup_b = (await client.post("/api/v1/suppliers", json={"name": "B Supplier"}, headers=headers_b)).json()
    r = await client.post(
        API,
        json={"supplier_id": sup_b["id"],
              "items": [{"product_id": None, "product_name": "Misc", "quantity": "1", "total_item_cost": "500"}]},
        headers=headers_a,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_supplier"


# --------------------------------------------------------------------------- #
# Warnings
# --------------------------------------------------------------------------- #
async def test_duplicate_product_warning(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers, name="Repeated", stock_qty="0")
    r = await client.post(
        API,
        json={"items": [
            {"product_id": product["id"], "product_name": "Repeated", "quantity": "5", "total_item_cost": "5000"},
            {"product_id": product["id"], "product_name": "Repeated", "quantity": "3", "total_item_cost": "3000"},
        ]},
        headers=headers,
    )
    assert r.status_code == 201
    warnings = r.json()["warnings"]
    assert any(w["code"] == "DUPLICATE_PRODUCT" for w in warnings)
    assert r.json()["purchase"]["total_buying_cost"] == "8000.00"


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
async def test_history_scoped_to_business(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700930001")
    headers_b, _ = await setup_business(client, phone="+256700930002")
    await client.post(
        API,
        json={"items": [{"product_id": None, "product_name": "X", "quantity": "1", "total_item_cost": "500"}]},
        headers=headers_a,
    )
    a = await client.get(API, headers=headers_a)
    b = await client.get(API, headers=headers_b)
    assert a.json()["total"] == 1 and b.json()["total"] == 0


async def test_history_filter_by_supplier(client: AsyncClient):
    headers, _ = await setup_business(client)
    sup = (await client.post("/api/v1/suppliers", json={"name": "ACME"}, headers=headers)).json()
    await client.post(
        API,
        json={"supplier_id": sup["id"],
              "items": [{"product_id": None, "product_name": "X", "quantity": "1", "total_item_cost": "500"}]},
        headers=headers,
    )
    await client.post(
        API,
        json={"items": [{"product_id": None, "product_name": "Y", "quantity": "1", "total_item_cost": "100"}]},
        headers=headers,
    )
    r = await client.get(f"{API}?supplier_id={sup['id']}", headers=headers)
    assert r.json()["total"] == 1
