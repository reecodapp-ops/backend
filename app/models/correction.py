"""Correction audit log and lookup, mirroring dukamate_schema_v3.sql section 5.

CORRECTION WORKFLOW (per blueprint F12)
---------------------------------------
Financial amounts are NEVER updated in place. To correct a record:

  1. Flip the original record to ``is_correction = TRUE`` so that
     ``reconcile_business_cash`` excludes it (the function filters on
     ``is_correction = FALSE``). The original row remains intact for audit.
  2. Insert a NEW replacement record with the corrected values and
     ``original_<entity>_id`` pointing back to the original. The replacement is
     ``is_correction = FALSE`` so its aggregate trigger fires AND it is included
     in reconcile.
  3. Insert one row in ``corrections`` linking original_record_id ->
     corrected_record_id with the field/old/new/reason for audit.
  4. Call ``reconcile_business_cash(business_id)`` to repair cash_on_hand and
     total_debt_out from the canonical (non-correction) set.

NB: This module implements the monetary corrections that the reconcile function
already supports: SALE_EDIT, EXPENSE_EDIT, PAYMENT_EDIT, CAPITAL_EDIT. The other
seeded correction types (SALE_ITEM_EDIT, STOCK_EDIT, PRODUCT_COST_EDIT,
DEBT_WRITE_OFF) need entity-specific aggregate repair beyond reconcile_business_cash
and are out of scope here; PRODUCT_COST_EDIT is already handled in-place by the
product service per the blueprint's price-edit rule.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, uuid_pk

# Cross-dialect SMALLSERIAL pattern reused from other lookup models.
_SMALL_PK = Integer().with_variant(SmallInteger(), "postgresql")


class CorrectionType(Base):
    """Seeded lookup of correction kinds."""

    __tablename__ = "correction_types"

    id: Mapped[int] = mapped_column(_SMALL_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    target_table: Mapped[str] = mapped_column(String(60), nullable=False)


class Correction(Base):
    """Audit row: links the wrong record to its replacement and explains why."""

    __tablename__ = "corrections"

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    correction_type_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("correction_types.id"),
        nullable=False,
    )
    original_record_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    corrected_record_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    field_changed: Mapped[str | None] = mapped_column(String(100), nullable=True)
    old_value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
