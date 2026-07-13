"""customer payments: customer_payments + payment_allocations

Adds the Customer Payment tables matching dukamate_schema_v3.sql.

NOTE: The fn_after_payment_insert trigger (which performs oldest-first allocation
and updates customer/business balances + ledger + sale outstanding) lives in the
full schema file and is applied in production from dukamate_schema_v3.sql. This
migration provides the table structure only — trigger DDL is intentionally not
duplicated here to keep one source of truth for trigger logic.

KNOWN SCHEMA BUG: In dukamate_schema_v3.sql the fn_after_payment_insert FOR loop
selects only ``id, remaining_amount`` from customer_debt_ledger but later does
``UPDATE sales ... WHERE id = v_ledger.sale_id``. The loop SELECT must include
``sale_id`` for the trigger to work at all. The schema needs the column added to
that SELECT; the backend code in this module is correct against the trigger's
intended behavior as documented in the blueprint.

Revision ID: 0005_customer_payments
Revises: 0004_sales
Create Date: 2026-06-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_customer_payments"
down_revision: Union[str, None] = "0004_sales"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- customer_payments ---------------------------------------------- #
    op.create_table(
        "customer_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_correction", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("original_payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["original_payment_id"], ["customer_payments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="customer_payments_amount_check"),
    )
    op.create_index("idx_customer_payments_business", "customer_payments", ["business_id"])
    op.create_index("idx_customer_payments_customer", "customer_payments", ["customer_id"])
    op.create_index("idx_customer_payments_occurred", "customer_payments", ["business_id", "occurred_at"])

    # ----- payment_allocations -------------------------------------------- #
    op.create_table(
        "payment_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("debt_ledger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("allocated_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_id"], ["customer_payments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["debt_ledger_id"], ["customer_debt_ledger.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("allocated_amount > 0", name="payment_allocations_amount_check"),
    )
    op.create_index("idx_payment_allocations_payment", "payment_allocations", ["payment_id"])
    op.create_index("idx_payment_allocations_ledger", "payment_allocations", ["debt_ledger_id"])


def downgrade() -> None:
    op.drop_index("idx_payment_allocations_ledger", table_name="payment_allocations")
    op.drop_index("idx_payment_allocations_payment", table_name="payment_allocations")
    op.drop_table("payment_allocations")
    op.drop_index("idx_customer_payments_occurred", table_name="customer_payments")
    op.drop_index("idx_customer_payments_customer", table_name="customer_payments")
    op.drop_index("idx_customer_payments_business", table_name="customer_payments")
    op.drop_table("customer_payments")
