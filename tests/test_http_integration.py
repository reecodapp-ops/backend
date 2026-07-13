"""Integration tests through the real HTTP stack (FastAPI + dependency
overrides for the DB only — auth, business-scoping, routing, and Pydantic
validation are all exercised for real via httpx against the ASGI app).
"""
from __future__ import annotations


async def _onboard_business(client) -> dict:
    resp = await client.post(
        "/api/v1/businesses",
        json={"shop_name": "Integration Shop", "business_type_id": 1, "currency_id": 1},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_full_customer_and_sale_flow_over_http(client):
    business = await _onboard_business(client)
    headers = {"X-Business-Id": business["id"]}

    # create a customer
    resp = await client.post(
        "/api/v1/customers", json={"full_name": "HTTP Customer"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    customer = resp.json()
    assert customer["credit_level"] == "UNSCORED"

    # credit profile
    resp = await client.get(f"/api/v1/customers/{customer['id']}/credit", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["recommendation"]

    # simulate credit
    resp = await client.post(
        f"/api/v1/customers/{customer['id']}/simulate-credit",
        json={"requested_amount": "5000"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["would_be_approved"] is True

    # a cash sale
    resp = await client.post(
        "/api/v1/sales",
        json={
            "payment_type": "CASH",
            "items": [{"product_name": "Notebook", "quantity": "2", "unit_price": "1500"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    sale = resp.json()["sale"]
    assert sale["total_amount"] == "3000.00"

    # receipt preview
    resp = await client.get(f"/api/v1/sales/{sale['id']}/receipt", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["business_name"] == "Integration Shop"


async def test_products_filter_over_http(client):
    business = await _onboard_business(client)
    headers = {"X-Business-Id": business["id"]}

    await client.post(
        "/api/v1/products",
        json={"name": "Widget A", "selling_price": "1000", "stock_qty": "0"},
        headers=headers,
    )
    await client.post(
        "/api/v1/products",
        json={"name": "Widget B", "selling_price": "2000", "stock_qty": "40"},
        headers=headers,
    )

    resp = await client.get(
        "/api/v1/products", params={"stock_status": "OUT_OF_STOCK"}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Widget A"


async def test_business_scoping_requires_header(client):
    await _onboard_business(client)
    resp = await client.get("/api/v1/customers")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "missing_business_id"


async def test_currencies_and_subscription_plans_lookup_endpoints(client):
    resp = await client.get("/api/v1/currencies")
    assert resp.status_code == 200
    codes = {c["iso_code"] for c in resp.json()}
    assert "UGX" in codes

    resp = await client.get("/api/v1/subscription-plans")
    assert resp.status_code == 200
    codes = {p["code"] for p in resp.json()}
    assert "FREE" in codes


async def test_change_subscription_over_http(client):
    business = await _onboard_business(client)
    resp = await client.post(
        f"/api/v1/businesses/{business['id']}/subscription",
        json={"plan_id": 2},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan_id"] == 2

    resp = await client.get(f"/api/v1/businesses/{business['id']}/subscriptions")
    assert resp.status_code == 200
    history = resp.json()["items"]
    assert len(history) == 2
    plan_ids = {s["plan_id"] for s in history}
    assert plan_ids == {1, 2}


async def test_insights_endpoint_over_http(client):
    business = await _onboard_business(client)
    headers = {"X-Business-Id": business["id"]}

    # give it some activity so at least the cash-flow card renders sensibly
    await client.post(
        "/api/v1/customers", json={"full_name": "HTTP Insights Customer"}, headers=headers
    )
    await client.post(
        "/api/v1/sales",
        json={
            "payment_type": "CASH",
            "items": [{"product_name": "Pen", "quantity": "1", "unit_price": "500"}],
        },
        headers=headers,
    )

    resp = await client.get(f"/api/v1/businesses/{business['id']}/insights")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["business_id"] == business["id"]
    assert any(i["category"] == "CASH_FLOW" for i in body["items"])


async def test_insights_endpoint_requires_ownership_over_http(client):
    business = await _onboard_business(client)
    # simulate a different, unauthorized user by forging a bad business id
    resp = await client.get(f"/api/v1/businesses/{uuid_module_fake_id()}/insights")
    assert resp.status_code == 404


def uuid_module_fake_id() -> str:
    import uuid

    return str(uuid.uuid4())
