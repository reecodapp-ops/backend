"""Customer model — mirrors the ``customers`` table in dukamate_schema_v3.sql.

Cached debt aggregates (debt_balance, oldest_unpaid_at, total_paid,
total_debt_issued, last_transaction_at) are maintained by DB triggers in the
Sales/Payments modules; the master-data module reads them and only writes the
profile fields and status.

CUSTOMER CREDIBILITY ENGINE [NEW v3]
-------------------------------------
``credit_score``, ``credit_level``, ``is_credit_blocked``,
``recommended_credit_limit``, ``average_payment_days``, ``late_payment_count``,
``on_time_payment_count``, ``fully_paid_debt_count`` and ``last_credit_review``
are written ONLY by the database function ``recompute_customer_credit_score()``
(fired after every debt sale and every payment, and nightly). The application
layer treats every one of these columns as read-only, exactly like the older
debt aggregates — never assign to them from a service or repository.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    created_at_column,
    updated_at_column,
    uuid_pk,
)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')", name="customers_status_check"
        ),
        CheckConstraint(
            "credit_level IN "
            "('UNSCORED','BLOCKED','HIGH_RISK','AVERAGE','GOOD','EXCELLENT')",
            name="customers_credit_level_check",
        ),
        CheckConstraint(
            "credit_score IS NULL OR credit_score BETWEEN 0 AND 100",
            name="customers_credit_score_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cached debt aggregates (trigger-maintained)
    debt_balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    oldest_unpaid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_paid: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    total_debt_issued: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    last_transaction_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ----- Customer Credibility Engine [NEW v3] — system-computed only -----
    credit_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    credit_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'UNSCORED'"),
        default="UNSCORED",
    )
    is_credit_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE"), default=False
    )
    recommended_credit_limit: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    average_payment_days: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 1), nullable=True
    )
    late_payment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    on_time_payment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    fully_paid_debt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    last_credit_review: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'"), default="ACTIVE"
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Customer id={self.id} name={self.full_name!r}>"
