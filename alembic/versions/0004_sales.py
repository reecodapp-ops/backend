"""sales: payment_types, sales, sale_items, customer_debt_ledger

Adds the Sales-module tables matching dukamate_schema_v3.sql, including the
seeded payment_types (CASH, ON_DEBT). Generated columns (gross_margin,
line_total, line_margin, remaining_amount) are created as Computed/STORED.

NOTE: The aggregate triggers (fn_after_sale_insert, fn_after_sale_item_insert)
and the validate trigger live in the full schema file and are applied in
production from dukamate_schema_v3.sql. This migration provides the table
structure for backend-managed environments; trigger DDL is intentionally not
duplicated here to keep a single source of truth for trigger logic.

Revision ID: 0004_sales
Revises: 0003_master_data
Create Date: 2026-05-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_sales"
down_revision: Union[str, None] = "0003_master_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- payment_types (lookup, seeded) --------------------------------- #
    payment_types = op.create_table(
        "payment_types",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.bulk_insert(
        payment_types,
        [
            {"id": 1, "code": "CASH", "label": "Paid now", "sort_order": 1},
            {"id": 2, "code": "ON_DEBT", "label": "On credit", "sort_order": 2},
        ],
    )

    # ----- sales ----------------------------------------------------------- #
    op.create_table(
        "sales",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payment_type_id", sa.SmallInteger(), nullable=False),
        sa.Column("total_amount", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("total_buying_cost", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "gross_margin",
            sa.Numeric(15, 2),
            sa.Computed("total_amount - total_buying_cost", persisted=True),
            nullable=True,
        ),
        sa.Column("outstanding_amount", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("receipt_number", sa.String(length=30), nullable=True),
        sa.Column("receipt_shared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_correction", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("original_sale_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_type_id"], ["payment_types.id"]),
        sa.ForeignKeyConstraint(["original_sale_id"], ["sales.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sales_business", "sales", ["business_id"])
    op.create_index("idx_sales_customer", "sales", ["customer_id"])
    op.create_index("idx_sales_occurred", "sales", ["business_id", "occurred_at"])
    op.create_index("idx_sales_receipt", "sales", ["business_id", "receipt_number"])

    # ----- sale_items ------------------------------------------------------ #
    op.create_table(
        "sale_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sale_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("product_name_snapshot", sa.String(length=300), nullable=False),
        sa.Column("quantity", sa.Numeric(15, 4), nullable=False),
        sa.Column("unit_price", sa.Numeric(15, 2), nullable=False),
        sa.Column("buying_cost", sa.Numeric(15, 2), nullable=True),
        sa.Column(
            "line_total",
            sa.Numeric(15, 2),
            sa.Computed("quantity * unit_price", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "line_margin",
            sa.Numeric(15, 2),
            sa.Computed("quantity * unit_price - COALESCE(quantity * buying_cost, 0)", persisted=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity > 0", name="sale_items_quantity_check"),
        sa.CheckConstraint("unit_price >= 0", name="sale_items_unit_price_check"),
    )
    op.create_index("idx_sale_items_sale", "sale_items", ["sale_id"])
    op.create_index("idx_sale_items_product", "sale_items", ["product_id"])

    # ----- customer_debt_ledger ------------------------------------------- #
    op.create_table(
        "customer_debt_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sale_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "remaining_amount",
            sa.Numeric(15, 2),
            sa.Computed("original_amount - paid_amount", persisted=True),
            nullable=True,
        ),
        sa.Column("incurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("fully_paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'UNPAID'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sale_id", name="uq_debt_ledger_sale"),
        sa.CheckConstraint("original_amount > 0", name="debt_ledger_original_check"),
        sa.CheckConstraint("paid_amount >= 0", name="debt_ledger_paid_check"),
        sa.CheckConstraint(
            "status IN ('UNPAID', 'PARTIAL', 'PAID', 'WRITTEN_OFF')",
            name="debt_ledger_status_check",
        ),
    )
    op.create_index("idx_debt_ledger_customer", "customer_debt_ledger", ["customer_id"])
    op.create_index("idx_debt_ledger_status", "customer_debt_ledger", ["business_id", "status"])


def downgrade() -> None:
    op.drop_table("customer_debt_ledger")
    op.drop_index("idx_sale_items_product", table_name="sale_items")
    op.drop_index("idx_sale_items_sale", table_name="sale_items")
    op.drop_table("sale_items")
    op.drop_index("idx_sales_receipt", table_name="sales")
    op.drop_index("idx_sales_occurred", table_name="sales")
    op.drop_index("idx_sales_customer", table_name="sales")
    op.drop_index("idx_sales_business", table_name="sales")
    op.drop_table("sales")
    op.drop_table("payment_types")
