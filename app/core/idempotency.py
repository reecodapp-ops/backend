"""Idempotency helpers for money-moving POST endpoints.

Usage in a service method::

    cached = await get_cached_response(session, business_id, key, "POST /sales")
    if cached is not None:
        return SaleCreateResponse.model_validate(cached)

    ... do the actual work, build `response` ...

    if key is not None:
        await store_response(session, business_id, key, "POST /sales", response)
    return response

RACE CONDITION — READ THIS BEFORE RELYING ON THIS FOR STRICT DEDUPLICATION
-----------------------------------------------------------------------------
This is the "simplest implementation" deliberately: check-then-act, no
row-level locking, no serializable transaction. Two requests carrying the
same key that arrive genuinely concurrently can both pass the "not seen yet"
check before either has written its row, both do the real work, and both
attempt to store their response — the second `store_response` call will hit
the table's unique constraint on ``(business_id, key, endpoint)`` and is
caught here and silently ignored (the caller still returns the response it
itself computed). In that narrow window, the underlying record (a sale, a
payment) IS created twice.

What this DOES reliably prevent is the far more common case this feature
exists for: a client retries a POST after a timeout/dropped-connection once
the original request has already completed — the retry arrives, finds the
key already stored, and gets the original response back with no new record
created at all.

If true exactly-once semantics under concurrent duplicate submission is ever
required, that needs a stronger primitive (e.g. `SELECT ... FOR UPDATE` on a
pre-inserted "claim" row, or a serializable transaction) — a deliberately
separate, larger change from what was asked for here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.idempotency import IdempotencyKey

# "within the last 24h" per the task spec — a key older than this is treated
# as expired and the request proceeds normally (and overwrites/re-stores).
DEFAULT_TTL = timedelta(hours=24)


async def get_cached_response(
    session: AsyncSession,
    business_id: uuid.UUID,
    key: str | None,
    endpoint: str,
    *,
    ttl: timedelta = DEFAULT_TTL,
) -> dict[str, Any] | None:
    """Return the previously-stored response body for this
    (business_id, key, endpoint), or ``None`` if there's no usable header,
    no prior record, or the prior record has expired."""
    if not key:
        return None

    cutoff = datetime.now(timezone.utc) - ttl
    result = await session.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.business_id == business_id,
            IdempotencyKey.key == key,
            IdempotencyKey.endpoint == endpoint,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None

    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at < cutoff:
        return None  # expired -- treat as if never seen

    return row.response_body


async def store_response(
    session: AsyncSession,
    business_id: uuid.UUID,
    key: str | None,
    endpoint: str,
    response_body: dict[str, Any],
) -> None:
    """Persist the response for future replay. No-op if no key was supplied.

    Runs inside a SAVEPOINT (``begin_nested``): if a genuinely concurrent
    duplicate write conflicts on the unique constraint, only this nested
    block rolls back — NOT the outer transaction, which by this point
    already has the real sale/payment insert pending in it. A plain
    ``session.rollback()`` here would silently discard that record too.
    """
    if not key:
        return

    try:
        async with session.begin_nested():
            session.add(
                IdempotencyKey(
                    business_id=business_id,
                    key=key,
                    endpoint=endpoint,
                    response_body=response_body,
                )
            )
            await session.flush()
    except IntegrityError:
        # Another request with the same key beat us to it — that's fine,
        # this caller still returns the response it computed itself.
        pass
