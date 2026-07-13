"""Supplier model — mirrors the ``suppliers`` table in dukamate_schema_v3.sql.

[NEW — Reecod Scoring Engine] ``credit_terms_days`` and ``payment_flexibility``
support the Supplier Terms Score (Reecod Engine Logic Spec §4.2). Both are
optional, owner-entered fields — if unset, the Terms score simply drops that
factor and redistributes its weight (see app/services/scoring_service.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    created_at_column,
    updated_at_column,
    uuid_pk,
)


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')", name="suppliers_status_check"
        ),
        CheckConstraint(
            "payment_flexibility IS NULL OR payment_flexibility IN "
            "('FLEXIBLE','SOMETIMES_FLEXIBLE','STRICT_CASH','UNKNOWN')",
            name="suppliers_payment_flexibility_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ----- Reecod Scoring Engine: Supplier Terms inputs [NEW] -----------------
    credit_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_flexibility: Mapped[str | None] = mapped_column(String(20), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'"), default="ACTIVE"
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Supplier id={self.id} name={self.name!r}>"
