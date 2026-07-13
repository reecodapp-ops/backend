"""Business Setup test suite.

Follows blueprint Section 8 (Business Setup):
- Happy: create sets cash_on_hand = opening_balance and assigns FREE plan in one transaction.
- Validation: invalid business_type_id; negative opening_balance; bad currency.
- Edge: ownership enforcement, not found, partial update, status change.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient

from tests.conftest import (
    auth_headers,
    business_payload,
    register_and_login,
)

API = "/api/v1/businesses"

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
async def test_create_business_onboarding(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])

    r = await client.post(API, json=business_payload(), headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()

    # owner is the authenticated user
    assert body["owner_id"] == session["user"]["id"]
    assert body["shop_name"] == "Joy's Supermarket"
    assert body["business_type_id"] == 1
    assert body["currency_code"] == "UGX"
    # currency_symbol defaults to the code when not provided
    assert body["currency_symbol"] == "UGX"
    # opening balance seeds cash_on_hand exactly (counted once)
    assert Decimal(body["opening_balance"]) == Decimal("500000.00")
    assert Decimal(body["cash_on_hand"]) == Decimal("500000.00")
    assert Decimal(body["total_debt_out"]) == Decimal("0")
    assert body["status"] == "ACTIVE"
    # FREE subscription assigned in the same transaction
    assert body["active_subscription"] is not None
    assert body["active_subscription"]["plan_id"] == 1
    assert body["active_subscription"]["is_active"] is True


async def test_create_then_get_business(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    created = (await client.post(API, json=business_payload(), headers=headers)).json()

    r = await client.get(f"{API}/{created['id']}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["id"]
    assert body["active_subscription"]["plan_id"] == 1


async def test_zero_opening_balance_defaults(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    r = await client.post(
        API,
        json=business_payload(opening_balance="0", opening_date=None),
        headers=headers,
    )
    assert r.status_code == 201
    assert Decimal(r.json()["cash_on_hand"]) == Decimal("0")


# --------------------------------------------------------------------------- #
# Validation failures
# --------------------------------------------------------------------------- #
async def test_create_invalid_business_type(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    r = await client.post(
        API, json=business_payload(business_type_id=999), headers=headers
    )
    assert r.status_code == 422 or r.status_code == 400
    # service raises ValidationError -> our envelope
    if r.status_code == 422 and "error" in r.json():
        assert r.json()["error"]["code"] == "invalid_business_type"


async def test_create_negative_opening_balance(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    r = await client.post(
        API, json=business_payload(opening_balance="-100"), headers=headers
    )
    assert r.status_code == 422  # pydantic ge=0


async def test_create_empty_shop_name(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    r = await client.post(
        API, json=business_payload(shop_name="   "), headers=headers
    )
    assert r.status_code == 422


async def test_create_bad_currency(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    r = await client.post(
        API, json=business_payload(currency_code="shillings"), headers=headers
    )
    assert r.status_code == 422


async def test_create_requires_auth(client: AsyncClient):
    r = await client.post(API, json=business_payload())
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Update / settings
# --------------------------------------------------------------------------- #
async def test_update_business_settings(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    created = (await client.post(API, json=business_payload(), headers=headers)).json()

    r = await client.patch(
        f"{API}/{created['id']}",
        json={"shop_name": "New Name", "location": "Jinja", "currency_code": "KES"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["shop_name"] == "New Name"
    assert body["location"] == "Jinja"
    assert body["currency_code"] == "KES"
    # symbol follows code when not explicitly set
    assert body["currency_symbol"] == "KES"


async def test_update_status_to_archived(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    created = (await client.post(API, json=business_payload(), headers=headers)).json()

    r = await client.patch(
        f"{API}/{created['id']}", json={"status": "ARCHIVED"}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ARCHIVED"


async def test_update_cannot_change_balances(client: AsyncClient):
    """opening_balance / cash_on_hand are not part of the update schema -> ignored."""
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    created = (await client.post(API, json=business_payload(), headers=headers)).json()

    r = await client.patch(
        f"{API}/{created['id']}",
        json={"cash_on_hand": "999999", "opening_balance": "999999"},
        headers=headers,
    )
    # Extra fields are ignored by the update schema; balances unchanged.
    assert r.status_code == 200
    assert Decimal(r.json()["cash_on_hand"]) == Decimal("500000.00")


# --------------------------------------------------------------------------- #
# Edge cases — ownership and not found
# --------------------------------------------------------------------------- #
async def test_get_nonexistent_business(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    r = await client.get(f"{API}/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "business_not_found"


async def test_cannot_access_other_users_business(client: AsyncClient):
    # user A creates a business
    a = await register_and_login(client, phone="+256700000001")
    headers_a = auth_headers(a["access_token"])
    created = (await client.post(API, json=business_payload(), headers=headers_a)).json()

    # user B tries to read it
    b = await register_and_login(client, phone="+256700000002")
    headers_b = auth_headers(b["access_token"])

    r_get = await client.get(f"{API}/{created['id']}", headers=headers_b)
    assert r_get.status_code == 403
    assert r_get.json()["error"]["code"] == "business_not_owned"

    r_patch = await client.patch(
        f"{API}/{created['id']}", json={"shop_name": "Hijacked"}, headers=headers_b
    )
    assert r_patch.status_code == 403


async def test_owner_can_have_multiple_businesses(client: AsyncClient):
    session = await register_and_login(client)
    headers = auth_headers(session["access_token"])
    r1 = await client.post(API, json=business_payload(shop_name="Shop One"), headers=headers)
    r2 = await client.post(API, json=business_payload(shop_name="Shop Two"), headers=headers)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
