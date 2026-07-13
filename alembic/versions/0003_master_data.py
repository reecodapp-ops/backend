"""master data: customers, suppliers, product_categories, products

Adds the master-data tables matching dukamate_schema_v3.sql, including the
seeded system-default product categories (business_id NULL). RLS policies and
triggers from the full schema are applied separately via the schema file in
production; this migration provides the table structure for backend-managed
environments.

Revision ID: 0003_master_data
Revises: 0002_business_setup
Create Date: 2026-05-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_master_data"
down_revision: Union[str, None] = "0002_business_setup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- customers ------------------------------------------------------- #
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("debt_balance", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("oldest_unpaid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_paid", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("total_debt_issued", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("last_transaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="customers_status_check"),
    )
    op.create_index("idx_customers_business", "customers", ["business_id"])
    op.create_index("idx_customers_status", "customers", ["business_id", "status"])
    op.create_index("idx_customers_debt", "customers", ["business_id", "debt_balance"])

    # ----- suppliers ------------------------------------------------------- #
    op.create_table(
        "suppliers",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="suppliers_status_check"),
    )
    op.create_index("idx_suppliers_business", "suppliers", ["business_id"])

    # ----- product_categories --------------------------------------------- #
    categories = op.create_table(
        "product_categories",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("icon_name", sa.String(length=100), nullable=True),
        sa.Column("color_hex", sa.String(length=7), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "name", name="uq_product_categories_biz_name"),
    )
    op.create_index("idx_product_categories_business", "product_categories", ["business_id"])
    op.bulk_insert(
        categories,
        [
            {"business_id": None, "name": "Food & Groceries", "icon_name": "lunch_dining", "color_hex": None, "sort_order": 1, "is_system": True, "is_active": True},
            {"business_id": None, "name": "Drinks", "icon_name": "local_bar", "color_hex": None, "sort_order": 2, "is_system": True, "is_active": True},
            {"business_id": None, "name": "Cleaning", "icon_name": "cleaning_services", "color_hex": None, "sort_order": 3, "is_system": True, "is_active": True},
            {"business_id": None, "name": "Medicine", "icon_name": "medication", "color_hex": None, "sort_order": 4, "is_system": True, "is_active": True},
            {"business_id": None, "name": "Hardware", "icon_name": "build", "color_hex": None, "sort_order": 5, "is_system": True, "is_active": True},
            {"business_id": None, "name": "Clothing", "icon_name": "checkroom", "color_hex": None, "sort_order": 6, "is_system": True, "is_active": True},
            {"business_id": None, "name": "Electronics", "icon_name": "devices", "color_hex": None, "sort_order": 7, "is_system": True, "is_active": True},
            {"business_id": None, "name": "Other", "icon_name": "category", "color_hex": None, "sort_order": 8, "is_system": True, "is_active": True},
        ],
    )

    # ----- products -------------------------------------------------------- #
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", sa.SmallInteger(), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("selling_price", sa.Numeric(15, 2), nullable=True),
        sa.Column("buying_price", sa.Numeric(15, 2), nullable=True),
        sa.Column("stock_on_hand", sa.Numeric(15, 4), server_default=sa.text("0"), nullable=False),
        sa.Column("low_stock_level", sa.Numeric(15, 4), server_default=sa.text("5"), nullable=False),
        sa.Column("avg_buying_cost", sa.Numeric(15, 2), nullable=True),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("image_thumbnail_url", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("last_sold_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_restocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["product_categories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="products_status_check"),
    )
    op.create_index("idx_products_business", "products", ["business_id"])
    op.create_index("idx_products_category", "products", ["category_id"])
    op.create_index("idx_products_status", "products", ["business_id", "status"])
    op.create_index("idx_products_stock", "products", ["business_id", "stock_on_hand"])


def downgrade() -> None:
    op.drop_table("products")
    op.drop_index("idx_product_categories_business", table_name="product_categories")
    op.drop_table("product_categories")
    op.drop_index("idx_suppliers_business", table_name="suppliers")
    op.drop_table("suppliers")
    op.drop_index("idx_customers_debt", table_name="customers")
    op.drop_index("idx_customers_status", table_name="customers")
    op.drop_index("idx_customers_business", table_name="customers")
    op.drop_table("customers")
