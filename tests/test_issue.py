"""Issue / Decision Engine module tests.

Service logic on SQLite: recompute generates issues from view data,
re-recompute is convergent (same data -> no new issues), conditions that go
away SUPERSEDE the corresponding issue. RESOLVED/IGNORED status transitions are
also tested.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import create_customer, create_product, setup_business

API = "/api/v1/issues"
pytestmark = pytest.mark.asyncio


async def test_recompute_creates_stock_finishing_issue(client: AsyncClient):
    headers, _ = await setup_business(client)
    # OUT_OF_STOCK item -> ALERT severity STOCK_FINISHING issue
    await create_product(client, headers, name="Empty Item", stock_qty="0", low_stock_level="5")

    r = await client.post(f"{API}/recompute", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["created"] >= 1
    assert body["superseded"] == 0

    issues = (await client.get(API, headers=headers)).json()["items"]
    types = {i["issue_type_code"] for i in issues}
    assert "STOCK_FINISHING" in types
    stock_issue = next(i for i in issues if i["issue_type_code"] == "STOCK_FINISHING")
    assert stock_issue["severity"] == "ALERT"
    assert stock_issue["status"] == "ACTIVE"
    assert "Empty Item" in stock_issue["headline"]
    from decimal import Decimal
    assert Decimal(stock_issue["data_snapshot"]["stock_on_hand"]) == 0


async def test_recompute_is_convergent(client: AsyncClient):
    """Running the engine twice on identical data produces no new issues."""
    headers, _ = await setup_business(client)
    await create_product(client, headers, name="LowItem", stock_qty="2", low_stock_level="5")

    r1 = await client.post(f"{API}/recompute", headers=headers)
    body1 = r1.json()
    assert body1["created"] >= 1

    r2 = await client.post(f"{API}/recompute", headers=headers)
    body2 = r2.json()
    assert body2["created"] == 0
    assert body2["superseded"] == 0
    assert body2["unchanged"] >= 1


async def test_resolved_condition_supersedes_issue(client: AsyncClient):
    """When the underlying condition goes away, the active issue is SUPERSEDED."""
    headers, _ = await setup_business(client)
    p = await create_product(client, headers, name="Maybe", stock_qty="1", low_stock_level="5")
    pid = p["id"]
    await client.post(f"{API}/recompute", headers=headers)
    issues = (await client.get(API, headers=headers)).json()["items"]
    assert any(i["ref_product_id"] == pid for i in issues)

    # Patch stock up so the condition no longer holds (PATCH /products doesn't
    # set stock directly; do a direct stock purchase instead).
    await client.post(
        "/api/v1/stock-purchases",
        json={"items": [{"product_id": pid, "product_name": "Maybe", "quantity": "20", "total_item_cost": "20000"}]},
        headers=headers,
    )
    # Note: SQLite has no item trigger so stock isn't bumped automatically. To
    # simulate, also patch the product's low_stock_level downward.
    await client.patch(
        f"/api/v1/products/{pid}",
        json={"low_stock_level": "0.0001"},
        headers=headers,
    )

    r = await client.post(f"{API}/recompute", headers=headers)
    body = r.json()
    assert body["superseded"] >= 1

    issues_after = (await client.get(f"{API}?status=SUPERSEDED", headers=headers)).json()
    assert any(i["ref_product_id"] == pid for i in issues_after["items"])


async def test_patch_issue_resolved(client: AsyncClient):
    headers, _ = await setup_business(client)
    await create_product(client, headers, name="X", stock_qty="0", low_stock_level="5")
    await client.post(f"{API}/recompute", headers=headers)
    issues = (await client.get(API, headers=headers)).json()["items"]
    issue_id = issues[0]["id"]

    r = await client.patch(f"{API}/{issue_id}", json={"status": "RESOLVED"}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "RESOLVED"
    assert body["resolved_at"] is not None


async def test_patch_issue_ignored(client: AsyncClient):
    headers, _ = await setup_business(client)
    await create_product(client, headers, name="X", stock_qty="0", low_stock_level="5")
    await client.post(f"{API}/recompute", headers=headers)
    issue_id = (await client.get(API, headers=headers)).json()["items"][0]["id"]
    r = await client.patch(f"{API}/{issue_id}", json={"status": "IGNORED"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "IGNORED"


async def test_patch_non_active_issue_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    await create_product(client, headers, name="X", stock_qty="0", low_stock_level="5")
    await client.post(f"{API}/recompute", headers=headers)
    issue_id = (await client.get(API, headers=headers)).json()["items"][0]["id"]
    # first transition is allowed
    await client.patch(f"{API}/{issue_id}", json={"status": "RESOLVED"}, headers=headers)
    # second transition on the same issue is rejected
    r = await client.patch(f"{API}/{issue_id}", json={"status": "IGNORED"}, headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "issue_not_active"


async def test_patch_unknown_issue_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.patch(
        f"{API}/{uuid.uuid4()}", json={"status": "RESOLVED"}, headers=headers
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "issue_not_found"


async def test_list_filter_by_severity(client: AsyncClient):
    headers, _ = await setup_business(client)
    await create_product(client, headers, name="EmptyA", stock_qty="0", low_stock_level="5")  # ALERT
    await create_product(client, headers, name="LowB", stock_qty="2", low_stock_level="5")    # WARN
    await client.post(f"{API}/recompute", headers=headers)

    alerts = (await client.get(f"{API}?severity=ALERT", headers=headers)).json()
    warns = (await client.get(f"{API}?severity=WARN", headers=headers)).json()
    assert all(i["severity"] == "ALERT" for i in alerts["items"])
    assert all(i["severity"] == "WARN" for i in warns["items"])


async def test_issues_scoped_to_business(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256712400001")
    headers_b, _ = await setup_business(client, phone="+256712400002")
    await create_product(client, headers_a, name="A-Empty", stock_qty="0", low_stock_level="5")
    await create_product(client, headers_b, name="B-Empty", stock_qty="0", low_stock_level="5")
    await client.post(f"{API}/recompute", headers=headers_a)
    await client.post(f"{API}/recompute", headers=headers_b)

    a_issues = (await client.get(API, headers=headers_a)).json()["items"]
    b_issues = (await client.get(API, headers=headers_b)).json()["items"]
    a_headlines = [i["headline"] for i in a_issues]
    b_headlines = [i["headline"] for i in b_issues]
    assert any("A-Empty" in h for h in a_headlines)
    assert all("A-Empty" not in h for h in b_headlines)
