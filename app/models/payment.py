"""Customer payment models — mirror dukamate_schema_v3.sql.

TRIGGER-BASED ARCHITECTURE (Strategy A)
---------------------------------------
The service inserts ONLY the ``customer_payments`` row. The database trigger
``fn_after_payment_insert`` then does everything else automatically:
  1. customers.debt_balance -= amount, total_paid += amount, last_transaction_at
  2. businesses.cash_on_hand += amount, total_debt_out -= amount
  3. allocates the amount oldest-first across open customer_debt_ledger rows,
     inserting payment_allocations rows and updating each ledger's paid_amount /
     status / fully_paid_at and the linked sale's outstanding_amount
  4. recomputes customers.oldest_unpaid_at

PaymentAllocation is therefore read-only from the service's perspective: rows are
created by the trigger and read back to build the response.
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
    Numeric,
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


class CustomerPayment(Base):
    __tablename__ = "customer_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="customer_payments_amount_check"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_correction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE"), default=False
    )
    original_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("customer_payments.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CustomerPayment id={self.id} amount={self.amount}>"


class PaymentAllocation(Base):
    """Read-only mapping. Rows are created by fn_after_payment_insert."""

    __tablename__ = "payment_allocations"
    __table_args__ = (
        CheckConstraint(
            "allocated_amount > 0", name="payment_allocations_amount_check"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("customer_payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    debt_ledger_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("customer_debt_ledger.id", ondelete="RESTRICT"),
        nullable=False,
    )
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PaymentAllocation payment={self.payment_id} amount={self.allocated_amount}>"
