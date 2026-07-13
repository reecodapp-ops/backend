"""expenses + stock purchases + capital source types

Adds:
  - expense_category_groups + expense_categories (with seeded system rows)
  - capital_source_types (seeded)
  - expenses
  - stock_purchases + stock_purchase_items

Triggers (fn_after_expense_insert, fn_after_stock_purchase_insert,
fn_after_stock_purchase_item_insert, fn_after_capital_txn_insert) live in the
full schema file and are applied in production from dukamate_schema_v3.sql.

Revision ID: 0006_money_modules
Revises: 0005_customer_payments
Create Date: 2026-06-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_money_modules"
down_revision: Union[str, None] = "0005_customer_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- expense_category_groups ---------------------------------------- #
    groups = op.create_table(
        "expense_category_groups",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.bulk_insert(
        groups,
        [
            {"id": 1, "code": "BUSINESS", "label": "Business expense", "sort_order": 1},
            {"id": 2, "code": "PERSONAL", "label": "Personal use", "sort_order": 2},
        ],
    )

    # ----- expense_categories --------------------------------------------- #
    categories = op.create_table(
        "expense_categories",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.SmallInteger(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("icon_name", sa.String(length=100), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["expense_category_groups.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "business_id", name="uq_expense_categories_code_biz"),
    )
    op.bulk_insert(
        categories,
        [
            {"id": 1, "group_id": 1, "code": "TRANSPORT", "label": "Transport", "icon_name": "local_shipping", "sort_order": 1, "is_system": True, "is_active": True, "business_id": None},
            {"id": 2, "group_id": 1, "code": "RENT", "label": "Rent", "icon_name": "apartment", "sort_order": 2, "is_system": True, "is_active": True, "business_id": None},
            {"id": 3, "group_id": 1, "code": "ELECTRICITY", "label": "Electricity", "icon_name": "bolt", "sort_order": 3, "is_system": True, "is_active": True, "business_id": None},
            {"id": 4, "group_id": 1, "code": "AIRTIME_DATA", "label": "Airtime/data", "icon_name": "wifi_calling", "sort_order": 4, "is_system": True, "is_active": True, "business_id": None},
            {"id": 5, "group_id": 1, "code": "WATER", "label": "Water", "icon_name": "water_drop", "sort_order": 5, "is_system": True, "is_active": True, "business_id": None},
            {"id": 6, "group_id": 1, "code": "STOCK_EXPENSE", "label": "Stock", "icon_name": "inventory", "sort_order": 6, "is_system": True, "is_active": True, "business_id": None},
            {"id": 7, "group_id": 2, "code": "HOME", "label": "Home", "icon_name": "home", "sort_order": 7, "is_system": True, "is_active": True, "business_id": None},
            {"id": 8, "group_id": 2, "code": "SCHOOL_FEES", "label": "School fees", "icon_name": "school", "sort_order": 8, "is_system": True, "is_active": True, "business_id": None},
            {"id": 9, "group_id": 2, "code": "FAMILY_SUPPORT", "label": "Family support", "icon_name": "group", "sort_order": 9, "is_system": True, "is_active": True, "business_id": None},
            {"id": 10, "group_id": 2, "code": "HEALTHCARE", "label": "Healthcare", "icon_name": "health_and_safety", "sort_order": 10, "is_system": True, "is_active": True, "business_id": None},
            {"id": 11, "group_id": 2, "code": "SAVINGS", "label": "Savings", "icon_name": "savings", "sort_order": 11, "is_system": True, "is_active": True, "business_id": None},
        ],
    )

    # ----- capital_source_types ------------------------------------------- #
    sources = op.create_table(
        "capital_source_types",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.bulk_insert(
        sources,
        [
            {"id": 1, "code": "PERSONAL_SAVINGS", "label": "Personal savings", "sort_order": 1},
            {"id": 2, "code": "BANK_LOAN", "label": "Bank loan", "sort_order": 2},
            {"id": 3, "code": "MOBILE_LOAN", "label": "Mobile loan", "sort_order": 3},
            {"id": 4, "code": "FAMILY_FRIEND", "label": "Family / friend", "sort_order": 4},
            {"id": 5, "code": "INVESTOR", "label": "Investor", "sort_order": 5},
            {"id": 6, "code": "OTHER", "label": "Other", "sort_order": 6},
        ],
    )

    # ----- expenses ------------------------------------------------------- #
    op.create_table(
        "expenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", sa.SmallInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("supplier_name", sa.String(length=200), nullable=True),
        sa.Column("is_correction", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("original_expense_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["expense_categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["original_expense_id"], ["expenses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="expenses_amount_check"),
    )
    op.create_index("idx_expenses_business", "expenses", ["business_id"])
    op.create_index("idx_expenses_occurred", "expenses", ["business_id", "occurred_at"])
    op.create_index("idx_expenses_category", "expenses", ["business_id", "category_id"])

    # ----- stock_purchases ------------------------------------------------ #
    op.create_table(
        "stock_purchases",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("supplier_name_free", sa.String(length=200), nullable=True),
        sa.Column("total_buying_cost", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_correction", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("original_purchase_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["original_purchase_id"], ["stock_purchases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_stock_purchases_business", "stock_purchases", ["business_id"])
    op.create_index("idx_stock_purchases_supplier", "stock_purchases", ["supplier_id"])
    op.create_index("idx_stock_purchases_occurred", "stock_purchases", ["business_id", "occurred_at"])

    # ----- stock_purchase_items ------------------------------------------- #
    op.create_table(
        "stock_purchase_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("product_name_snapshot", sa.String(length=300), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 4), nullable=False),
        sa.Column("unit_buying_cost", sa.Numeric(15, 2), nullable=True),
        sa.Column("total_item_cost", sa.Numeric(15, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["purchase_id"], ["stock_purchases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity > 0", name="stock_purchase_items_qty_check"),
        sa.CheckConstraint("total_item_cost >= 0", name="stock_purchase_items_total_check"),
    )
    op.create_index("idx_stock_purchase_items_purchase", "stock_purchase_items", ["purchase_id"])
    op.create_index("idx_stock_purchase_items_product", "stock_purchase_items", ["product_id"])

    # capital_transactions.source_type_id FK -> add it now that capital_source_types exists.
    op.create_foreign_key(
        "fk_capital_txn_source",
        "capital_transactions",
        "capital_source_types",
        ["source_type_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_capital_txn_source", "capital_transactions", type_="foreignkey")
    op.drop_table("stock_purchase_items")
    op.drop_table("stock_purchases")
    op.drop_table("expenses")
    op.drop_table("capital_source_types")
    op.drop_table("expense_categories")
    op.drop_table("expense_category_groups")
