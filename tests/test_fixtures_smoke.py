"""Sanity check for the shared fixtures themselves — if this fails, every
other test file's failures are probably fixture problems, not app bugs."""
from __future__ import annotations

import pytest


async def test_business_ctx_fixture_creates_business(business_ctx):
    assert business_ctx.business.shop_name == "Test Shop"
    assert business_ctx.business.currency.iso_code == "UGX"


async def test_client_health_check(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_client_can_create_business_over_http(client):
    resp = await client.post(
        "/api/v1/businesses",
        json={
            "shop_name": "HTTP Shop",
            "business_type_id": 1,
            "currency_id": 1,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["shop_name"] == "HTTP Shop"
    assert body["currency"]["iso_code"] == "UGX"
    assert body["active_subscription"]["plan_id"] == 1
