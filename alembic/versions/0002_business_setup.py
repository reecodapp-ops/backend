"""business setup tables: lookups, businesses, subscriptions, capital_transactions

Adds the tables needed by the Business Setup module, matching
dukamate_schema_v3.sql. Lookup tables (business_types, subscription_plans,
capital_transaction_types) are created AND seeded here so a fresh database is
immediately usable. capital_transactions is created with the columns needed for
the opening-balance/audit flow (full capital module wiring arrives later but the
table shape matches the schema).

Revision ID: 0002_business_setup
Revises: 0001_auth_tables
Create Date: 2026-05-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_business_setup"
down_revision: Union[str, None] = "0001_auth_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- lookup: business_types ----------------------------------------- #
    business_types = op.create_table(
        "business_types",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("icon_name", sa.String(length=100), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.bulk_insert(
        business_types,
        [
            {"id": 1, "code": "RETAIL_SHOP", "label": "Retail shop", "icon_name": "shopping_basket", "sort_order": 1, "is_active": True},
            {"id": 2, "code": "BOUTIQUE", "label": "Boutique", "icon_name": "checkroom", "sort_order": 2, "is_active": True},
            {"id": 3, "code": "WHOLESALE", "label": "Wholesale", "icon_name": "inventory_2", "sort_order": 3, "is_active": True},
            {"id": 4, "code": "PHARMACY", "label": "Pharmacy", "icon_name": "medical_services", "sort_order": 4, "is_active": True},
            {"id": 5, "code": "HARDWARE", "label": "Hardware", "icon_name": "handyman", "sort_order": 5, "is_active": True},
            {"id": 6, "code": "OTHER", "label": "Other", "icon_name": "apps", "sort_order": 6, "is_active": True},
        ],
    )

    # ----- lookup: subscription_plans ------------------------------------- #
    subscription_plans = op.create_table(
        "subscription_plans",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("engine_phase_max", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("price_monthly", sa.Numeric(10, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("currency_code", sa.String(length=10), server_default=sa.text("'UGX'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.bulk_insert(
        subscription_plans,
        [
            {"id": 1, "code": "FREE", "label": "Free", "engine_phase_max": 1, "price_monthly": 0, "currency_code": "UGX", "is_active": True},
            {"id": 2, "code": "STARTER", "label": "Starter", "engine_phase_max": 2, "price_monthly": 0, "currency_code": "UGX", "is_active": True},
            {"id": 3, "code": "PRO", "label": "Pro", "engine_phase_max": 4, "price_monthly": 0, "currency_code": "UGX", "is_active": True},
        ],
    )

    # ----- lookup: capital_transaction_types ------------------------------ #
    capital_types = op.create_table(
        "capital_transaction_types",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("direction", sa.String(length=1), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.CheckConstraint("direction IN ('I', 'O')", name="capital_txn_type_dir_check"),
    )
    op.bulk_insert(
        capital_types,
        [
            {"id": 1, "code": "OPENING_BALANCE", "label": "Opening balance", "direction": "I", "sort_order": 1},
            {"id": 2, "code": "MONEY_IN", "label": "Money added", "direction": "I", "sort_order": 2},
            {"id": 3, "code": "MONEY_OUT", "label": "Money removed", "direction": "O", "sort_order": 3},
            {"id": 4, "code": "PERSONAL_USE", "label": "Personal use", "direction": "O", "sort_order": 4},
        ],
    )

    # ----- businesses ------------------------------------------------------ #
    op.create_table(
        "businesses",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_type_id", sa.SmallInteger(), nullable=False),
        sa.Column("shop_name", sa.String(length=200), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=True),
        sa.Column("location", sa.String(length=300), nullable=True),
        sa.Column("currency_code", sa.String(length=10), server_default=sa.text("'UGX'"), nullable=False),
        sa.Column("currency_symbol", sa.String(length=10), server_default=sa.text("'UGX'"), nullable=False),
        sa.Column("opening_balance", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("opening_date", sa.Date(), server_default=sa.text("CURRENT_DATE"), nullable=False),
        sa.Column("cash_on_hand", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("total_debt_out", sa.Numeric(15, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("logo_url", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_type_id"], ["business_types.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'ARCHIVED')", name="businesses_status_check"),
    )
    op.create_index("idx_businesses_owner", "businesses", ["owner_id"])
    op.create_index("idx_businesses_status", "businesses", ["status"])

    # ----- business_subscriptions ----------------------------------------- #
    op.create_table(
        "business_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", sa.SmallInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["subscription_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_biz_subscriptions_business", "business_subscriptions", ["business_id"])

    # ----- capital_transactions ------------------------------------------- #
    op.create_table(
        "capital_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type_id", sa.SmallInteger(), nullable=False),
        sa.Column("source_type_id", sa.SmallInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_correction", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("original_txn_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["type_id"], ["capital_transaction_types.id"]),
        sa.ForeignKeyConstraint(["original_txn_id"], ["capital_transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="capital_txn_amount_check"),
    )
    op.create_index("idx_capital_txn_business", "capital_transactions", ["business_id"])
    op.create_index("idx_capital_txn_type", "capital_transactions", ["business_id", "type_id"])
    op.create_index("idx_capital_txn_occurred", "capital_transactions", ["business_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("capital_transactions")
    op.drop_index("idx_biz_subscriptions_business", table_name="business_subscriptions")
    op.drop_table("business_subscriptions")
    op.drop_index("idx_businesses_status", table_name="businesses")
    op.drop_index("idx_businesses_owner", table_name="businesses")
    op.drop_table("businesses")
    op.drop_table("capital_transaction_types")
    op.drop_table("subscription_plans")
    op.drop_table("business_types")
