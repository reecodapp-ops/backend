"""Customer module tests: create, update, detail, search, archive + scoping."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import setup_business

API = "/api/v1/customers"
pytestmark = pytest.mark.asyncio


async def test_create_customer(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"full_name": "Bosco Mukasa", "phone_number": "+256701234567"}, headers=headers
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["full_name"] == "Bosco Mukasa"
    assert body["debt_balance"] == "0.00"
    assert body["status"] == "ACTIVE"


async def test_create_requires_business_header(client: AsyncClient):
    headers, _ = await setup_business(client)
    no_biz = {k: v for k, v in headers.items() if k != "X-Business-Id"}
    r = await client.post(API, json={"full_name": "X"}, headers=no_biz)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_business_id"


async def test_create_empty_name_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(API, json={"full_name": "  "}, headers=headers)
    assert r.status_code == 422


async def test_create_bad_phone(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"full_name": "Bad Phone", "phone_number": "0701"}, headers=headers
    )
    assert r.status_code == 422


async def test_get_and_update_customer(client: AsyncClient):
    headers, _ = await setup_business(client)
    created = (await client.post(API, json={"full_name": "Janet W"}, headers=headers)).json()
    cid = created["id"]

    r = await client.get(f"{API}/{cid}", headers=headers)
    assert r.status_code == 200

    r = await client.patch(
        f"{API}/{cid}", json={"full_name": "Janet Wekesa", "notes": "regular"}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["full_name"] == "Janet Wekesa"
    assert r.json()["notes"] == "regular"


async def test_get_nonexistent(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.get(f"{API}/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "customer_not_found"


async def test_archive_customer(client: AsyncClient):
    headers, _ = await setup_business(client)
    created = (await client.post(API, json={"full_name": "Tom Odhiambo"}, headers=headers)).json()
    cid = created["id"]

    r = await client.post(f"{API}/{cid}/archive", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "ARCHIVED"

    # default search (status=ACTIVE) should now exclude it
    r = await client.get(API, headers=headers)
    assert all(c["id"] != cid for c in r.json()["items"])

    # archived search includes it
    r = await client.get(f"{API}?status=ARCHIVED", headers=headers)
    assert any(c["id"] == cid for c in r.json()["items"])


async def test_search_by_name(client: AsyncClient):
    headers, _ = await setup_business(client)
    await client.post(API, json={"full_name": "Mercy Cherono"}, headers=headers)
    await client.post(API, json={"full_name": "George Akinyi"}, headers=headers)
    await client.post(API, json={"full_name": "Mercy Atieno"}, headers=headers)

    r = await client.get(f"{API}?search=mercy", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert all("Mercy" in c["full_name"] for c in items)


async def test_search_pagination(client: AsyncClient):
    headers, _ = await setup_business(client)
    for i in range(5):
        await client.post(API, json={"full_name": f"Cust {i}"}, headers=headers)
    r = await client.get(f"{API}?limit=2&offset=0", headers=headers)
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2


async def test_customers_isolated_between_businesses(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700000011")
    headers_b, _ = await setup_business(client, phone="+256700000022")

    await client.post(API, json={"full_name": "A-Customer"}, headers=headers_a)

    # business B sees none of A's customers
    r = await client.get(API, headers=headers_b)
    assert r.json()["total"] == 0


async def test_cannot_use_unowned_business_id(client: AsyncClient):
    headers_a, biz_a = await setup_business(client, phone="+256700000033")
    headers_b, _ = await setup_business(client, phone="+256700000044")

    # user B tries to act on business A by swapping the header
    forged = {**headers_b, "X-Business-Id": biz_a}
    r = await client.post(API, json={"full_name": "Hijack"}, headers=forged)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "business_not_owned"
