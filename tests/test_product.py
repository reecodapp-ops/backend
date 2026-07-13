"""Product & Category module tests.

Categories: create, list (system + own), update, system read-only guard.
Products: create (+duplicate warning), update (price-edit), detail, search, archive.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import setup_business

CAT_API = "/api/v1/product-categories"
PROD_API = "/api/v1/products"
pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #
async def test_list_includes_system_defaults(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.get(CAT_API, headers=headers)
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    # seeded system defaults are visible
    assert "Food & Groceries" in names
    assert "Medicine" in names
    assert all(c["is_system"] for c in r.json() if c["business_id"] is None)


async def test_create_custom_category(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        CAT_API, json={"name": "Airtime", "icon_name": "wifi", "color_hex": "#4CAF50"}, headers=headers
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Airtime"
    assert body["is_system"] is False
    assert body["business_id"] is not None


async def test_create_duplicate_category_name(client: AsyncClient):
    headers, _ = await setup_business(client)
    await client.post(CAT_API, json={"name": "Snacks"}, headers=headers)
    r = await client.post(CAT_API, json={"name": "Snacks"}, headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "category_name_taken"


async def test_bad_color_hex(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(CAT_API, json={"name": "X", "color_hex": "green"}, headers=headers)
    assert r.status_code == 422


async def test_update_custom_category(client: AsyncClient):
    headers, _ = await setup_business(client)
    created = (await client.post(CAT_API, json={"name": "Temp"}, headers=headers)).json()
    r = await client.patch(
        f"{CAT_API}/{created['id']}", json={"name": "Permanent", "sort_order": 5}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Permanent"
    assert r.json()["sort_order"] == 5


async def test_cannot_edit_system_category(client: AsyncClient):
    headers, _ = await setup_business(client)
    # category id 1 is a seeded system default
    r = await client.patch(f"{CAT_API}/1", json={"name": "Hacked"}, headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "system_category_readonly"


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #
async def test_create_product(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        PROD_API,
        json={
            "name": "Sugar 1kg",
            "category_id": 1,
            "unit": "kg",
            "selling_price": "4000.00",
            "buying_price": "3200.00",
            "stock_qty": "50",
            "low_stock_level": "10",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["product"]["name"] == "Sugar 1kg"
    # initial stock set directly
    assert body["product"]["stock_on_hand"] == "50.0000"
    # avg_buying_cost initialised from buying_price
    assert body["product"]["avg_buying_cost"] == "3200.00"
    assert body["warnings"] == []


async def test_create_product_duplicate_warning(client: AsyncClient):
    headers, _ = await setup_business(client)
    await client.post(PROD_API, json={"name": "Soap Bar"}, headers=headers)
    r = await client.post(PROD_API, json={"name": "Soap Bar Large"}, headers=headers)
    assert r.status_code == 201
    warnings = r.json()["warnings"]
    assert any(w["code"] == "DUPLICATE_ITEM" for w in warnings)


async def test_create_product_invalid_category(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        PROD_API, json={"name": "Item", "category_id": 9999}, headers=headers
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_category"


async def test_create_product_negative_price(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        PROD_API, json={"name": "Item", "selling_price": "-5"}, headers=headers
    )
    assert r.status_code == 422


async def test_update_product_price_edit(client: AsyncClient):
    headers, _ = await setup_business(client)
    created = (
        await client.post(
            PROD_API, json={"name": "Rice 1kg", "buying_price": "3500"}, headers=headers
        )
    ).json()["product"]
    pid = created["id"]

    r = await client.patch(
        f"{PROD_API}/{pid}", json={"buying_price": "3800", "selling_price": "5000"}, headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["buying_price"] == "3800.00"
    # avg_buying_cost baseline follows the new buying price
    assert body["avg_buying_cost"] == "3800.00"
    assert body["selling_price"] == "5000.00"


async def test_get_and_archive_product(client: AsyncClient):
    headers, _ = await setup_business(client)
    created = (
        await client.post(PROD_API, json={"name": "Cooking Oil 1L"}, headers=headers)
    ).json()["product"]
    pid = created["id"]

    r = await client.get(f"{PROD_API}/{pid}", headers=headers)
    assert r.status_code == 200

    r = await client.post(f"{PROD_API}/{pid}/archive", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "ARCHIVED"

    # excluded from default ACTIVE search
    r = await client.get(PROD_API, headers=headers)
    assert all(p["id"] != pid for p in r.json()["items"])


async def test_get_nonexistent_product(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.get(f"{PROD_API}/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "product_not_found"


async def test_search_products_by_name_and_category(client: AsyncClient):
    headers, _ = await setup_business(client)
    await client.post(PROD_API, json={"name": "Milk 500ml", "category_id": 1}, headers=headers)
    await client.post(PROD_API, json={"name": "Soda 500ml", "category_id": 2}, headers=headers)
    await client.post(PROD_API, json={"name": "Bread Loaf", "category_id": 1}, headers=headers)

    r = await client.get(f"{PROD_API}?search=500ml", headers=headers)
    assert r.json()["total"] == 2

    r = await client.get(f"{PROD_API}?category_id=2", headers=headers)
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["name"] == "Soda 500ml"


async def test_products_isolated_between_businesses(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700000077")
    headers_b, _ = await setup_business(client, phone="+256700000088")
    await client.post(PROD_API, json={"name": "A-Product"}, headers=headers_a)
    r = await client.get(PROD_API, headers=headers_b)
    assert r.json()["total"] == 0
