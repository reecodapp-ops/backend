"""drop legacy currency_code / currency_symbol columns

Revision ID: 0003_drop_legacy_currency_columns
Revises: 0002_add_currencies_and_location
Create Date: 2026-07-02

================================================================================
 DATA-LOSS WARNING — READ BEFORE RUNNING
================================================================================
This migration DROPS ``businesses.currency_code``, ``businesses.currency_symbol``
and ``subscription_plans.currency_code``. These columns are already unused by
the application as of migration 0002 (the ORM derives the same values
dynamically from ``currency_id`` -> ``currencies``), but the raw column data —
including any custom ``currency_symbol`` a business had set that *differed*
from the standard symbol for its ISO code — is destroyed once this migration
runs and cannot be recovered from the database alone afterwards.

DO NOT RUN THIS MIGRATION UNTIL:
  1. Migration 0002 has been deployed and run against production for at least
     one full cycle, and
  2. You have confirmed (e.g. via
     ``SELECT id, currency_code, currency_symbol FROM businesses b
        JOIN currencies c ON c.id = b.currency_id
        WHERE UPPER(b.currency_code) <> c.iso_code OR b.currency_symbol <> c.symbol;``)
     that there are zero rows where the legacy free-text values disagree with
     the backfilled ``currency_id`` — or that you are comfortable with the
     small number of disagreements found (e.g. a business had a locally
     customized currency_symbol that will no longer be respected once these
     columns are gone; that business will fall back to the standard symbol on
     the referenced ``currencies`` row).

If you are not ready to accept that, STOP — stay on migration 0002 and keep
reading currency data through ``currency_id`` while leaving the legacy columns
in the database indefinitely; they cost nothing to keep and this migration is
optional. Only run this once you have made a deliberate decision to remove them.
================================================================================

DOWNGRADE
---------
Recreates the three columns (nullable) and repopulates them from the current
``currency_id`` -> ``currencies`` relationship. This restores structurally
valid values (a real ISO code and its standard symbol) but CANNOT restore any
row where the original free-text value differed from the standard currency
row (e.g. a hand-typed custom symbol) — that information is genuinely gone.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_drop_legacy_currency_columns"
down_revision: Union[str, None] = "0002_add_currencies_and_location"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("businesses", "currency_code")
    op.drop_column("businesses", "currency_symbol")
    op.drop_column("subscription_plans", "currency_code")


def downgrade() -> None:
    op.add_column("businesses", sa.Column("currency_code", sa.String(10), nullable=True))
    op.add_column("businesses", sa.Column("currency_symbol", sa.String(10), nullable=True))
    op.add_column(
        "subscription_plans", sa.Column("currency_code", sa.String(10), nullable=True)
    )

    op.execute(
        sa.text(
            """
            UPDATE businesses b
            SET currency_code = c.iso_code, currency_symbol = c.symbol
            FROM currencies c
            WHERE c.id = b.currency_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE subscription_plans sp
            SET currency_code = c.iso_code
            FROM currencies c
            WHERE c.id = sp.currency_id
            """
        )
    )

    op.alter_column("businesses", "currency_code", nullable=False, server_default="UGX")
    op.alter_column("businesses", "currency_symbol", nullable=False, server_default="UGX")
