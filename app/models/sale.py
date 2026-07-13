"""Sale, SaleItem, and CustomerDebtLedger models — mirror dukamate_schema_v3.sql.

TRIGGER-BASED ARCHITECTURE (Strategy A)
---------------------------------------
The service inserts ``sales`` and ``sale_items`` rows ONLY. The database triggers
then do all aggregate work automatically:

  * trg_after_sale_insert (WHEN is_correction = FALSE):
      - CASH    -> businesses.cash_on_hand += total_amount
      - ON_DEBT -> customers.debt_balance / total_debt_issued / oldest_unpaid_at,
                   businesses.total_debt_out, AND inserts the customer_debt_ledger row
  * trg_after_sale_item_insert:
      - products.stock_on_hand -= quantity, sets last_sold_at

Consequences the service MUST respect:
  1. The sale trigger reads NEW.total_amount (it does NOT recompute from items),
     so total_amount / total_buying_cost must be computed in Python and set on the
     sales row BEFORE insert.
  2. No trigger sets sales.outstanding_amount on insert, so the service sets it
     (= total_amount for ON_DEBT, 0 for CASH).
  3. gross_margin / line_total / line_margin are GENERATED columns — never written.

generated columns are mapped read-only (no insert/update).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    created_at_column,
    updated_at_column,
    uuid_pk,
)


class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=True,
    )
    payment_type_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("payment_types.id"), nullable=False
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    total_buying_cost: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    # GENERATED ALWAYS AS (total_amount - total_buying_cost) STORED — read-only
    gross_margin: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        Computed("total_amount - total_buying_cost", persisted=True),
        nullable=True,
    )
    outstanding_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    # [NEW v3] Optional owner-set due date for debt sales. Copied onto the
    # trigger-created customer_debt_ledger row at insert time.
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    receipt_shared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # [NEW v3] Who the receipt was shared to: 'CUSTOMER' | 'BUSINESS' | 'OTHER'.
    receipt_shared_to: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_correction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE"), default=False
    )
    original_sale_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    items: Mapped[list["SaleItem"]] = relationship(
        back_populates="sale",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Sale id={self.id} receipt={self.receipt_number} total={self.total_amount}>"


class SaleItem(Base):
    __tablename__ = "sale_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="sale_items_quantity_check"),
        CheckConstraint("unit_price >= 0", name="sale_items_unit_price_check"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    sale_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sales.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
    )
    product_name_snapshot: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    buying_cost: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    # GENERATED columns — read-only
    line_total: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        Computed("quantity * unit_price", persisted=True),
        nullable=True,
    )
    line_margin: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        Computed(
            "quantity * unit_price - COALESCE(quantity * buying_cost, 0)",
            persisted=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = created_at_column()

    sale: Mapped["Sale"] = relationship(back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SaleItem product={self.product_name_snapshot} qty={self.quantity}>"


class CustomerDebtLedger(Base):
    """Read-only mapping of the debt ledger.

    Rows are created by the sale trigger (ON_DEBT) and mutated by the payment
    trigger; the Sales module never writes here directly. Mapped so debt sales
    can surface the created ledger entry and tests can assert on it.
    """

    __tablename__ = "customer_debt_ledger"
    __table_args__ = (
        UniqueConstraint("sale_id", name="uq_debt_ledger_sale"),
        CheckConstraint("original_amount > 0", name="debt_ledger_original_check"),
        CheckConstraint("paid_amount >= 0", name="debt_ledger_paid_check"),
        CheckConstraint(
            "status IN ('UNPAID', 'PARTIAL', 'PAID', 'WRITTEN_OFF')",
            name="debt_ledger_status_check",
        ),
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
    sale_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sales.id", ondelete="RESTRICT"),
        nullable=False,
    )
    original_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    remaining_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        Computed("original_amount - paid_amount", persisted=True),
        nullable=True,
    )
    incurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # [FIX v3] The schema declares this DATE, not TIMESTAMPTZ — align the
    # mapping so due-date comparisons against CURRENT_DATE behave identically
    # in Python-side validation and in the DB triggers/functions.
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fully_paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'UNPAID'"), default="UNPAID"
    )
    # [NEW v3] Maintained by recompute_customer_credit_score() / the nightly
    # job — never written by the application. True while this entry is
    # currently overdue (past due_date, or open >30 days with no due_date).
    risk_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE"), default=False
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CustomerDebtLedger sale={self.sale_id} status={self.status}>"
