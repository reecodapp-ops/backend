"""Declarative base for all ORM models.

A single ``Base`` shared by every model so Alembic autogenerate and metadata
operations see the full schema. UUID and timestamp helpers live here to keep
models aligned with the database (gen_random_uuid() defaults, TIMESTAMPTZ).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base."""


def uuid_pk() -> Mapped[uuid.UUID]:
    """Primary key column matching the schema's ``DEFAULT uuid_generate_v7()``.

    [FIX] Previously this used ``gen_random_uuid()`` as the server default,
    which diverges from every table definition in dukamate_schema_v3.sql
    (``id UUID PRIMARY KEY DEFAULT uuid_generate_v7()``). Time-ordered v7 UUIDs
    matter here specifically because they are index-efficient (monotonically
    increasing, unlike v4's random scatter) and offline-sync-safe for the
    Drift/Flutter client, per the schema's own UUID v7 helper comment. Aligned
    so that whichever layer performs the insert (SQLAlchemy default or a raw
    SQL/administrative INSERT) produces the same kind of identifier.
    default=uuid.uuid4 remains as the Python-side fallback for backends
    without ``uuid_generate_v7()`` (e.g. SQLite in tests) — it only fires when
    the DB does not supply the server_default itself.
    """
    return mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuid_generate_v7()"),
        default=uuid.uuid4,
    )


def created_at_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


def updated_at_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
