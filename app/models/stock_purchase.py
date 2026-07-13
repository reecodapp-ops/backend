"""Stock purchase models — mirror dukamate_schema_v3.sql.

TRIGGER-BASED ARCHITECTURE (Strategy A)
---------------------------------------
The service inserts ONLY the raw stock_purchases + stock_purchase_items rows.
The DB triggers then:
  - fn_after_stock_purchase_insert  : businesses.cash_on_hand -= total_buying_cost
  - fn_after_stock_purchase_item_insert : products.stock_on_hand += quantity,
                                          sets last_restocked_at

Service responsibilities (the trigger cannot do these):
  1. Validate product_ids belong to the business.
  2. Compute total_buying_cost in Python and set it on the header BEFORE insert
     (the trigger reads NEW.total_buying_cost).
  3. Recompute products.avg_buying_cost as a weighted average (no trigger maintains it).

[NEW — Reecod Scoring Engine] ``promised_delivery_date`` / ``actual_delivery_date``
on the header, and ``quantity_ordered`` on each line, support the Supplier
Reliability Score (Reecod Engine Logic Spec §4.1). A stock purchase in this
schema represents a *delivered* order — there's no separate "order placed"
stage — so these fields let an owner optionally record what was promised vs.
what actually happened for a given delivery. All are nullable and backward
compatible: a purchase with no promised/actual dates simply doesn't count
toward Delivery Punctuality; a line with no ``quantity_ordered`` is treated as
fully filled (``quantity_ordered = quantity`` received) for Order Fill Rate.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
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


class StockPurchase(Base):
    __tablename__ = "stock_purchases"

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )
    supplier_name_free: Mapped[str | None] = mapped_column(String(200), nullable=True)
    total_buying_cost: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    # ----- Reecod Scoring Engine: Supplier Reliability inputs [NEW] -----------
    promised_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_correction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE"), default=False
    )
    original_purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("stock_purchases.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    items: Mapped[list["StockPurchaseItem"]] = relationship(
        back_populates="purchase",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class StockPurchaseItem(Base):
    __tablename__ = "stock_purchase_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="stock_purchase_items_qty_check"),
        CheckConstraint(
            "total_item_cost >= 0", name="stock_purchase_items_total_check"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    purchase_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("stock_purchases.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
    )
    product_name_snapshot: Mapped[str] = mapped_column(String(300), nullable=False)
    # Quantity actually received (pre-existing column — used elsewhere for
    # stock-on-hand increments; keep the name for backward compatibility).
    quantity: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False)
    # [NEW] Quantity that was ordered, if different from what was received.
    # NULL means "not tracked" -> Order Fill Rate treats it as fully filled.
    quantity_ordered: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 4), nullable=True
    )
    unit_buying_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    total_item_cost: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    purchase: Mapped["StockPurchase"] = relationship(back_populates="items")
