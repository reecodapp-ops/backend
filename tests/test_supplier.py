"""Supplier module tests: create, update, search, archive + scoping."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import setup_business

API = "/api/v1/suppliers"
pytestmark = pytest.mark.asyncio


async def test_create_supplier(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"name": "Mukwano Distributors", "phone_number": "+256702000111"}, headers=headers
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Mukwano Distributors"
    assert r.json()["status"] == "ACTIVE"


async def test_create_empty_name(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(API, json={"name": "   "}, headers=headers)
    assert r.status_code == 422


async def test_update_supplier(client: AsyncClient):
    headers, _ = await setup_business(client)
    created = (await client.post(API, json={"name": "Bidco"}, headers=headers)).json()
    r = await client.patch(
        f"{API}/{created['id']}", json={"name": "Bidco Wholesalers", "notes": "fast"}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Bidco Wholesalers"


async def test_archive_via_update(client: AsyncClient):
    headers, _ = await setup_business(client)
    created = (await client.post(API, json={"name": "Old Supplier"}, headers=headers)).json()
    r = await client.patch(
        f"{API}/{created['id']}", json={"status": "ARCHIVED"}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ARCHIVED"

    # excluded from default ACTIVE search
    r = await client.get(API, headers=headers)
    assert all(s["id"] != created["id"] for s in r.json()["items"])


async def test_search_suppliers(client: AsyncClient):
    headers, _ = await setup_business(client)
    await client.post(API, json={"name": "Nile Breweries Depot"}, headers=headers)
    await client.post(API, json={"name": "Coca-Cola Distributor"}, headers=headers)
    r = await client.get(f"{API}?search=nile", headers=headers)
    assert r.json()["total"] == 1
    assert "Nile" in r.json()["items"][0]["name"]


async def test_get_nonexistent_supplier(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.get(f"{API}/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "supplier_not_found"


async def test_suppliers_isolated(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700000055")
    headers_b, _ = await setup_business(client, phone="+256700000066")
    await client.post(API, json={"name": "A-Supplier"}, headers=headers_a)
    r = await client.get(API, headers=headers_b)
    assert r.json()["total"] == 0
