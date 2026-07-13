"""Capital Transaction module tests.

Service-layer tests on SQLite. The trigger-driven cash +/- is verified end-to-end
against real PostgreSQL.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import setup_business

API = "/api/v1/capital-transactions"
SOURCES_API = "/api/v1/capital-source-types"
pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Source types lookup
# --------------------------------------------------------------------------- #
async def test_list_source_types(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.get(SOURCES_API, headers=headers)
    assert r.status_code == 200
    codes = [s["code"] for s in r.json()]
    assert "PERSONAL_SAVINGS" in codes
    assert "BANK_LOAN" in codes
    assert "INVESTOR" in codes


# --------------------------------------------------------------------------- #
# MONEY_IN
# --------------------------------------------------------------------------- #
async def test_money_in_with_source(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={"type_code": "MONEY_IN", "source_type_id": 1, "amount": "200000",
              "note": "personal savings injection"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    txn = r.json()["capital_transaction"]
    assert txn["type_code"] == "MONEY_IN"
    assert txn["direction"] == "I"
    assert txn["amount"] == "200000.00"
    assert txn["source_type_id"] == 1


async def test_money_in_without_source(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"type_code": "MONEY_IN", "amount": "50000"}, headers=headers
    )
    assert r.status_code == 201
    assert r.json()["capital_transaction"]["source_type_id"] is None


# --------------------------------------------------------------------------- #
# MONEY_OUT / PERSONAL_USE
# --------------------------------------------------------------------------- #
async def test_money_out_basic(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"type_code": "MONEY_OUT", "amount": "30000"}, headers=headers
    )
    assert r.status_code == 201
    txn = r.json()["capital_transaction"]
    assert txn["direction"] == "O"
    assert txn["type_code"] == "MONEY_OUT"


async def test_personal_use_clears_source(client: AsyncClient):
    """source_type_id is meaningful only for MONEY_IN; for outflows it is dropped."""
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={"type_code": "PERSONAL_USE", "source_type_id": 1, "amount": "10000"},
        headers=headers,
    )
    assert r.status_code == 201
    txn = r.json()["capital_transaction"]
    assert txn["type_code"] == "PERSONAL_USE"
    assert txn["direction"] == "O"
    assert txn["source_type_id"] is None


# --------------------------------------------------------------------------- #
# Validation failures
# --------------------------------------------------------------------------- #
async def test_opening_balance_not_allowed(client: AsyncClient):
    """OPENING_BALANCE is recorded only at onboarding; the endpoint refuses it
    explicitly to prevent double-counting with businesses.opening_balance."""
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"type_code": "OPENING_BALANCE", "amount": "100000"}, headers=headers
    )
    # Pydantic Literal rejects it at schema layer (422)
    assert r.status_code == 422


async def test_zero_amount_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"type_code": "MONEY_IN", "amount": "0"}, headers=headers
    )
    assert r.status_code == 422


async def test_negative_amount_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API, json={"type_code": "MONEY_IN", "amount": "-100"}, headers=headers
    )
    assert r.status_code == 422


async def test_invalid_source_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={"type_code": "MONEY_IN", "source_type_id": 999, "amount": "10000"},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_capital_source"


async def test_requires_business_header(client: AsyncClient):
    headers, _ = await setup_business(client)
    no_biz = {k: v for k, v in headers.items() if k != "X-Business-Id"}
    r = await client.post(
        API, json={"type_code": "MONEY_IN", "amount": "1000"}, headers=no_biz
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "missing_business_id"


# --------------------------------------------------------------------------- #
# History / filtering
# --------------------------------------------------------------------------- #
async def test_history_filter_by_type(client: AsyncClient):
    headers, _ = await setup_business(client)
    await client.post(API, json={"type_code": "MONEY_IN", "amount": "100000"}, headers=headers)
    await client.post(API, json={"type_code": "MONEY_IN", "amount": "50000"}, headers=headers)
    await client.post(API, json={"type_code": "MONEY_OUT", "amount": "25000"}, headers=headers)
    r = await client.get(f"{API}?type=MONEY_IN", headers=headers)
    assert r.json()["total"] == 2
    r = await client.get(f"{API}?type=MONEY_OUT", headers=headers)
    assert r.json()["total"] == 1


async def test_history_scoped_to_business(client: AsyncClient):
    headers_a, _ = await setup_business(client, phone="+256700940001")
    headers_b, _ = await setup_business(client, phone="+256700940002")
    await client.post(API, json={"type_code": "MONEY_IN", "amount": "10000"}, headers=headers_a)
    a = await client.get(API, headers=headers_a)
    b = await client.get(API, headers=headers_b)
    assert a.json()["total"] == 1 and b.json()["total"] == 0
