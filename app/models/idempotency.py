"""Idempotency key model — safe replay of a POST that might have been sent
twice (flaky network, a double-tap, a retried request).

Only used by endpoints that create money-moving records where a duplicate
would be a real problem: ``POST /sales`` and ``POST /customer-payments``. A
client sends an ``Idempotency-Key`` header (its own generated UUID); if the
same ``(business_id, key, endpoint)`` is seen again, the ORIGINAL response is
replayed instead of creating a second record. See app/core/idempotency.py for
the read/write helpers and each service's ``create_sale`` /
``create_payment`` for how they're used.

Deliberately the "simplest implementation" per the requesting task: a single
table, checked before the main insert and written after a successful one, no
locking. This has a known, accepted race window (documented on
``app/core/idempotency.py``) — two truly concurrent requests with the same
key could both proceed and both get recorded, one at a time, with only the
first write staying due to the unique constraint on the second's redundant
write. That means the pair of underlying records can still be duplicated in
that narrow window; the guarantee this feature gives is against the much
more common case of sequential retries (the original + a retry that arrives
after the first request already completed).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    JSON,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, uuid_pk

# JSONB on PostgreSQL, plain JSON on SQLite for portability in tests.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "key", "endpoint", name="uq_idempotency_keys_scope"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Client-generated (a UUID is recommended but not enforced — any
    # reasonably unique string up to 200 chars is accepted).
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    # Which endpoint this key was scoped to, e.g. "POST /sales" — the same
    # key value could theoretically be reused across different endpoints by
    # an unrelated client without colliding.
    endpoint: Mapped[str] = mapped_column(String(100), nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<IdempotencyKey endpoint={self.endpoint} key={self.key!r}>"
