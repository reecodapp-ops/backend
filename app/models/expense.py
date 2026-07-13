"""Expense model — mirrors the ``expenses`` table.

TRIGGER: fn_after_expense_insert subtracts amount from businesses.cash_on_hand.
The service must NOT touch cash_on_hand itself.

Also defines the two seeded lookups (expense_category_groups, expense_categories)
because the master-data module didn't model them and the expense module needs
to validate category_id and surface category metadata.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
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

# Reuse the cross-dialect SMALLSERIAL-compatible PK pattern from product.py:
# Integer (SQLite rowid alias, autoincrements) variant SmallInteger on PostgreSQL.
_SMALL_PK = Integer().with_variant(SmallInteger(), "postgresql")


class ExpenseCategoryGroup(Base):
    """Seeded lookup: BUSINESS / PERSONAL."""

    __tablename__ = "expense_category_groups"

    id: Mapped[int] = mapped_column(_SMALL_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class ExpenseCategory(Base):
    """Seeded system categories + per-business custom categories.

    System categories have ``is_system = TRUE`` and ``business_id`` NULL.
    """

    __tablename__ = "expense_categories"
    __table_args__ = (
        UniqueConstraint("code", "business_id", name="uq_expense_categories_code_biz"),
    )

    id: Mapped[int] = mapped_column(_SMALL_PK, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("expense_category_groups.id", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    icon_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"), default=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"), default=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    category_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("expense_categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_correction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE"), default=False
    )
    original_expense_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("expenses.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
