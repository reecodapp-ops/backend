"""issue_types + issues + sync_events

Adds the Decision Engine tables (issue_types lookup, issues persistent cards)
and the offline-sync idempotency log (sync_events). Worker entrypoints reference
these tables.

Revision ID: 0008_issues_sync
Revises: 0007_corrections_dashboard
Create Date: 2026-06-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_issues_sync"
down_revision: Union[str, None] = "0007_corrections_dashboard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- issue_types (seeded lookup) ------------------------------------ #
    issue_types = op.create_table(
        "issue_types",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("engine_phase", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("severity_default", sa.String(length=10), server_default=sa.text("'INFO'"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.CheckConstraint(
            "severity_default IN ('INFO','WARN','ALERT')",
            name="issue_types_severity_check",
        ),
    )
    op.bulk_insert(
        issue_types,
        [
            {"id": 1, "code": "LOW_MARGIN", "label": "Item moving but leaving little money", "engine_phase": 1, "severity_default": "WARN", "description": "Gross margin below threshold"},
            {"id": 2, "code": "STOCK_FINISHING", "label": "This item may finish soon", "engine_phase": 1, "severity_default": "WARN", "description": "Days of stock < reorder lead time"},
            {"id": 3, "code": "SLOW_STOCK", "label": "Stock sitting too long", "engine_phase": 2, "severity_default": "INFO", "description": "No sales in N days"},
            {"id": 4, "code": "MONEY_PRESSURE", "label": "Money may become tight", "engine_phase": 2, "severity_default": "ALERT", "description": "Projected cash < threshold"},
            {"id": 5, "code": "DEBT_OVERDUE", "label": "Customer debt overdue", "engine_phase": 1, "severity_default": "WARN", "description": "Oldest debt exceeds N days"},
            {"id": 6, "code": "EXPENSE_PRESSURE", "label": "Expenses eating too much of sales", "engine_phase": 2, "severity_default": "WARN", "description": "Expense ratio above threshold"},
            {"id": 7, "code": "TRAPPED_CASH", "label": "Too much money still with customers", "engine_phase": 2, "severity_default": "WARN", "description": "Total debt > X% of revenue"},
            {"id": 8, "code": "RESTOCK_SUGGESTED", "label": "Consider restocking this item", "engine_phase": 2, "severity_default": "INFO", "description": "Stock below reorder level"},
            {"id": 9, "code": "PRICE_OPPORTUNITY", "label": "Selling price may be too low", "engine_phase": 4, "severity_default": "INFO", "description": "Margin benchmark signal"},
        ],
    )

    # ----- issues ---------------------------------------------------------- #
    op.create_table(
        "issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issue_type_id", sa.SmallInteger(), nullable=False),
        sa.Column("ref_product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ref_customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ref_sale_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confidence_level", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("severity", sa.String(length=10), server_default=sa.text("'INFO'"), nullable=False),
        sa.Column("headline", sa.String(length=300), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("suggested_action", sa.String(length=300), nullable=True),
        sa.Column("data_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issue_type_id"], ["issue_types.id"]),
        sa.ForeignKeyConstraint(["ref_product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ref_customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ref_sale_id"], ["sales.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("confidence_level BETWEEN 1 AND 4", name="issues_confidence_check"),
        sa.CheckConstraint("severity IN ('INFO','WARN','ALERT')", name="issues_severity_check"),
        sa.CheckConstraint("status IN ('ACTIVE','RESOLVED','IGNORED','SUPERSEDED')", name="issues_status_check"),
    )
    op.create_index("idx_issues_business_status", "issues", ["business_id", "status"])
    op.create_index("idx_issues_severity", "issues", ["business_id", "severity"])
    op.create_index("idx_issues_created", "issues", ["business_id", "created_at"])

    # ----- sync_events ----------------------------------------------------- #
    op.create_table(
        "sync_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("client_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("server_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "client_id", name="uq_sync_events_client"),
        sa.CheckConstraint(
            "status IN ('ACCEPTED','REJECTED','DUPLICATE')",
            name="sync_events_status_check",
        ),
    )
    op.create_index("idx_sync_events_business", "sync_events", ["business_id"])
    op.create_index("idx_sync_events_client", "sync_events", ["client_id"])


def downgrade() -> None:
    op.drop_table("sync_events")
    op.drop_table("issues")
    op.drop_table("issue_types")
