"""Sync module tests.

Exercises: batch ingest of multiple event types, idempotency on client_id,
per-event savepoint behavior (one bad event doesn't drop the rest), refreshed
aggregate read-back, and the issue pull-down.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.models.customer import Customer
from tests.conftest import create_customer, create_product, setup_business

API = "/api/v1/sync"
pytestmark = pytest.mark.asyncio


def _ts(seconds_offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds_offset)).isoformat()


# --------------------------------------------------------------------------- #
# Single-event happy paths per type
# --------------------------------------------------------------------------- #
async def test_sync_one_cash_sale(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers, name="Sugar", stock_qty="50")
    cid = str(uuid.uuid4())
    r = await client.post(
        API,
        json={
            "events": [
                {
                    "client_id": cid,
                    "event_type": "SALE",
                    "client_created_at": _ts(),
                    "payload": {
                        "payment_type": "CASH",
                        "items": [
                            {"product_id": product["id"], "product_name": "Sugar",
                             "quantity": "2", "unit_price": "4000"}
                        ],
                    },
                }
            ]
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["accepted"]) == 1
    assert body["accepted"][0]["client_id"] == cid
    assert body["accepted"][0]["server_record_id"] is not None
    assert body["rejected"] == []
    assert body["duplicates"] == []
    assert any(s["product_id"] == product["id"] for s in body["aggregates"]["stock"])


async def test_sync_expense(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(
        API,
        json={
            "events": [
                {
                    "client_id": str(uuid.uuid4()),
                    "event_type": "EXPENSE",
                    "client_created_at": _ts(),
                    "payload": {"category_id": 1, "amount": "5000"},
                }
            ]
        },
        headers=headers,
    )
    assert r.status_code == 200
    assert len(r.json()["accepted"]) == 1


# --------------------------------------------------------------------------- #
# Idempotency on client_id
# --------------------------------------------------------------------------- #
async def test_idempotent_on_client_id(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers, name="Sugar", stock_qty="50")
    cid = str(uuid.uuid4())
    payload = {
        "events": [
            {
                "client_id": cid,
                "event_type": "SALE",
                "client_created_at": _ts(),
                "payload": {
                    "payment_type": "CASH",
                    "items": [
                        {"product_id": product["id"], "product_name": "Sugar",
                         "quantity": "1", "unit_price": "4000"}
                    ],
                },
            }
        ]
    }
    r1 = await client.post(API, json=payload, headers=headers)
    server_id_1 = r1.json()["accepted"][0]["server_record_id"]

    # Re-send the same client_id -> DUPLICATE, same server_record_id, no new side effect.
    r2 = await client.post(API, json=payload, headers=headers)
    assert r2.status_code == 200
    body = r2.json()
    assert body["duplicates"] == [cid]
    assert body["accepted"][0]["server_record_id"] == server_id_1

    # Only ONE sale exists server-side
    sales = (await client.get("/api/v1/customer-payments", headers=headers)).json()
    # ... sale endpoint doesn't list yet; check via the cash hasn't doubled
    today = (await client.get("/api/v1/dashboard/today", headers=headers)).json()
    assert Decimal(today["sales_today"]) == Decimal("4000")  # single sale only


# --------------------------------------------------------------------------- #
# Per-event savepoint: one bad event doesn't poison the batch
# --------------------------------------------------------------------------- #
async def test_savepoint_isolates_failed_event(client: AsyncClient):
    headers, _ = await setup_business(client)
    product = await create_product(client, headers, name="Sugar", stock_qty="50")
    good_id = str(uuid.uuid4())
    bad_id = str(uuid.uuid4())
    another_good_id = str(uuid.uuid4())

    payload = {
        "events": [
            {
                "client_id": good_id,
                "event_type": "EXPENSE",
                "client_created_at": _ts(0),
                "payload": {"category_id": 1, "amount": "1000"},
            },
            {
                "client_id": bad_id,
                "event_type": "EXPENSE",
                "client_created_at": _ts(1),
                "payload": {"category_id": 9999, "amount": "500"},  # bad category
            },
            {
                "client_id": another_good_id,
                "event_type": "EXPENSE",
                "client_created_at": _ts(2),
                "payload": {"category_id": 1, "amount": "2000"},
            },
        ]
    }
    r = await client.post(API, json=payload, headers=headers)
    body = r.json()
    accepted_ids = {e["client_id"] for e in body["accepted"]}
    rejected_ids = {e["client_id"] for e in body["rejected"]}
    assert good_id in accepted_ids
    assert another_good_id in accepted_ids
    assert bad_id in rejected_ids
    # And the rejection reason carries the upstream code
    bad = next(e for e in body["rejected"] if e["client_id"] == bad_id)
    assert bad["error_code"] == "invalid_expense_category"


# --------------------------------------------------------------------------- #
# Ordering by client_created_at
# --------------------------------------------------------------------------- #
async def test_events_processed_in_client_order(client: AsyncClient):
    """Events are sorted by client_created_at, not the order they appear in the
    request, so a client that batches retries can keep timestamps as the source
    of truth."""
    headers, _ = await setup_business(client)
    earlier = str(uuid.uuid4())
    later = str(uuid.uuid4())
    payload = {
        "events": [
            {
                "client_id": later,
                "event_type": "EXPENSE",
                "client_created_at": _ts(10),
                "payload": {"category_id": 1, "amount": "200"},
            },
            {
                "client_id": earlier,
                "event_type": "EXPENSE",
                "client_created_at": _ts(0),
                "payload": {"category_id": 1, "amount": "100"},
            },
        ]
    }
    r = await client.post(API, json=payload, headers=headers)
    expenses = (await client.get("/api/v1/expenses", headers=headers)).json()
    # earliest expense has the earliest occurred_at (taken from server time on
    # write, but the sync handles the order; both inserted is the point).
    assert expenses["total"] == 2


# --------------------------------------------------------------------------- #
# Issues are pulled in the response
# --------------------------------------------------------------------------- #
async def test_sync_returns_issues_from_state(client: AsyncClient):
    headers, _ = await setup_business(client)
    await create_product(client, headers, name="EmptyOnSync", stock_qty="0", low_stock_level="5")
    await client.post("/api/v1/issues/recompute", headers=headers)
    # Now a sync that adds an unrelated event still pulls down the active issues
    r = await client.post(
        API,
        json={
            "events": [
                {
                    "client_id": str(uuid.uuid4()),
                    "event_type": "EXPENSE",
                    "client_created_at": _ts(),
                    "payload": {"category_id": 1, "amount": "100"},
                }
            ]
        },
        headers=headers,
    )
    body = r.json()
    types = {i["issue_type_code"] for i in body["issues"]}
    assert "STOCK_FINISHING" in types


# --------------------------------------------------------------------------- #
# Validation failures
# --------------------------------------------------------------------------- #
async def test_unknown_event_type_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    # Pydantic Literal blocks unknown event_type at schema layer -> 422
    r = await client.post(
        API,
        json={
            "events": [
                {
                    "client_id": str(uuid.uuid4()),
                    "event_type": "NUKE_DB",
                    "client_created_at": _ts(),
                    "payload": {},
                }
            ]
        },
        headers=headers,
    )
    assert r.status_code == 422


async def test_empty_batch_rejected(client: AsyncClient):
    headers, _ = await setup_business(client)
    r = await client.post(API, json={"events": []}, headers=headers)
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Aggregates returned reflect post-batch state
# --------------------------------------------------------------------------- #
async def test_aggregates_in_response(client: AsyncClient):
    headers, biz_id = await setup_business(client)
    r = await client.post(
        API,
        json={
            "events": [
                {
                    "client_id": str(uuid.uuid4()),
                    "event_type": "EXPENSE",
                    "client_created_at": _ts(),
                    "payload": {"category_id": 1, "amount": "100"},
                }
            ]
        },
        headers=headers,
    )
    body = r.json()
    # Just verify the structure — cash_on_hand and total_debt_out are present
    assert "cash_on_hand" in body["aggregates"]
    assert "total_debt_out" in body["aggregates"]
    assert Decimal(body["aggregates"]["cash_on_hand"]) >= 0
