"""fix uuid_generate_v7 function and align PK column defaults

Revision ID: 0001_fix_uuid_generate_v7
Revises:
Create Date: 2026-07-02

WHAT THIS MIGRATION DOES
-------------------------
1. Recreates ``uuid_generate_v7()`` with the corrected implementation from
   dukamate_schema_v3.sql. The previous version built a 33-hex-digit string
   (12 time digits + a stray literal '7' + 20 random digits) — an odd number
   of nibbles — which raised "invalid hexadecimal data" on every call. The
   fixed version assembles a clean 16-byte value (6 bytes timestamp + 10 bytes
   random) and sets the version/variant nibbles with SET_BYTE on the bytes
   directly instead of splicing hex text.

2. Changes every UUID primary key column's server-side DEFAULT from
   ``gen_random_uuid()`` to ``uuid_generate_v7()``, matching every table
   definition in dukamate_schema_v3.sql and the corresponding SQLAlchemy model
   change (``app/models/base.py::uuid_pk``).

WHY THIS IS SAFE (NO DATA LOSS)
---------------------------------
This migration only changes the DEFAULT *expression* evaluated for NEW rows
that omit an explicit id. It does not touch any existing row's stored id
value, so no backfill, no rewrite, and no risk to existing primary keys or
foreign keys pointing at them. The distinction only matters for INSERTs that
rely on the database to generate the id (e.g. raw SQL / admin tooling); the
application's SQLAlchemy layer already supplies a client-side UUID via
``default=uuid.uuid4`` on every insert, so this migration does not change
observable application behaviour — it only brings the database's own default
in line with the documented schema for anyone inserting outside the ORM.

DOWNGRADE
---------
Restores every listed column's default to ``gen_random_uuid()`` and leaves the
(corrected) ``uuid_generate_v7()`` function in place, since dropping it could
break anything else already depending on it; only the column defaults are
reverted.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_fix_uuid_generate_v7"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Every table whose `id` column is a UUID PRIMARY KEY with a DEFAULT expression
# (SMALLSERIAL lookup tables are excluded — they use their own integer
# sequences and are unaffected).
_UUID_PK_TABLES = [
    "users",
    "devices",
    "verification_codes",
    "businesses",
    "business_subscriptions",
    "capital_transactions",
    "customers",
    "suppliers",
    "products",
    "sales",
    "sale_items",
    "customer_debt_ledger",
    "customer_payments",
    "payment_allocations",
    "expenses",
    "stock_purchases",
    "stock_purchase_items",
    "corrections",
    "issues",
    "sync_events",
]

_CREATE_UUID_V7_FN = """
CREATE OR REPLACE FUNCTION uuid_generate_v7()
RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
    v_unix_ms    CONSTANT BIGINT := TRUNC(EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT;
    v_time_bytes          BYTEA  := DECODE(LPAD(TO_HEX(v_unix_ms), 12, '0'), 'hex'); -- 6 bytes
    v_rand_bytes          BYTEA  := gen_random_bytes(10);                            -- 10 bytes
    v_bytes               BYTEA;
BEGIN
    v_bytes := v_time_bytes || v_rand_bytes;                                  -- 16 bytes total
    v_bytes := SET_BYTE(v_bytes, 6, (GET_BYTE(v_bytes, 6) & 15) | 112);        -- version nibble = 7
    v_bytes := SET_BYTE(v_bytes, 8, (GET_BYTE(v_bytes, 8) & 63) | 128);        -- variant bits = 10
    RETURN ENCODE(v_bytes, 'hex')::UUID;
END;
$$;
"""


def upgrade() -> None:
    op.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
    op.execute(sa.text(_CREATE_UUID_V7_FN))

    for table in _UUID_PK_TABLES:
        op.execute(
            sa.text(
                f'ALTER TABLE {table} ALTER COLUMN id '
                f"SET DEFAULT uuid_generate_v7()"
            )
        )


def downgrade() -> None:
    for table in _UUID_PK_TABLES:
        op.execute(
            sa.text(
                f'ALTER TABLE {table} ALTER COLUMN id '
                f"SET DEFAULT gen_random_uuid()"
            )
        )
    # uuid_generate_v7() is intentionally left in place (see module docstring).
