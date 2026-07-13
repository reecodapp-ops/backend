"""corrections audit log + dashboard views

Adds:
  - correction_types (seeded lookup)
  - corrections audit table
  - v_daily_summary, v_debt_priority, v_debt_aging, v_stock_attention,
    v_product_margin_30d, v_expense_pressure_30d

KNOWN SCHEMA BUG (documented and patched here): The shipped v_daily_summary in
dukamate_schema_v3.sql LEFT JOINs sales × expenses × customer_payments, which
multiplies aggregated sums across the cartesian product. The version below uses
scalar subqueries per metric to compute each total independently. Verified
against a scenario with 2 sales + 1 expense + 1 payment that produced doubled
expense/payment totals from the original view, correct totals from this one.

Revision ID: 0007_corrections_dashboard
Revises: 0006_money_modules
Create Date: 2026-06-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_corrections_dashboard"
down_revision: Union[str, None] = "0006_money_modules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- correction_types (seeded lookup) ------------------------------- #
    ctypes = op.create_table(
        "correction_types",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("target_table", sa.String(length=60), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.bulk_insert(
        ctypes,
        [
            {"id": 1, "code": "SALE_EDIT", "label": "Sale amount corrected", "target_table": "sales"},
            {"id": 2, "code": "SALE_ITEM_EDIT", "label": "Sale item quantity/price edit", "target_table": "sale_items"},
            {"id": 3, "code": "EXPENSE_EDIT", "label": "Expense amount corrected", "target_table": "expenses"},
            {"id": 4, "code": "PAYMENT_EDIT", "label": "Customer payment corrected", "target_table": "customer_payments"},
            {"id": 5, "code": "STOCK_EDIT", "label": "Stock purchase quantity edit", "target_table": "stock_purchases"},
            {"id": 6, "code": "CAPITAL_EDIT", "label": "Capital transaction corrected", "target_table": "capital_transactions"},
            {"id": 7, "code": "PRODUCT_COST_EDIT", "label": "Product buying cost updated", "target_table": "products"},
            {"id": 8, "code": "DEBT_WRITE_OFF", "label": "Debt written off manually", "target_table": "customers"},
        ],
    )

    # ----- corrections audit table --------------------------------------- #
    op.create_table(
        "corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correction_type_id", sa.SmallInteger(), nullable=False),
        sa.Column("original_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("corrected_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("field_changed", sa.String(length=100), nullable=True),
        sa.Column("old_value_text", sa.Text(), nullable=True),
        sa.Column("new_value_text", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["correction_type_id"], ["correction_types.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_corrections_business", "corrections", ["business_id"])
    op.create_index("idx_corrections_original", "corrections", ["original_record_id"])
    op.create_index("idx_corrections_created", "corrections", ["business_id", "created_at"])

    # ----- dashboard views ----------------------------------------------- #
    # v_daily_summary — uses scalar subqueries to avoid the cartesian-product
    # over-counting bug present in dukamate_schema_v3.sql's original definition.
    op.execute("""
        CREATE VIEW v_daily_summary AS
        SELECT
            b.id AS business_id,
            CURRENT_DATE AS report_date,
            COALESCE((SELECT SUM(total_amount) FROM sales WHERE business_id=b.id AND DATE(occurred_at)=CURRENT_DATE AND is_correction=FALSE), 0) AS sales_today,
            COALESCE((SELECT SUM(total_amount) FROM sales WHERE business_id=b.id AND DATE(occurred_at)=CURRENT_DATE AND is_correction=FALSE AND payment_type_id=(SELECT id FROM payment_types WHERE code='CASH')), 0) AS cash_sales_today,
            COALESCE((SELECT SUM(total_amount) FROM sales WHERE business_id=b.id AND DATE(occurred_at)=CURRENT_DATE AND is_correction=FALSE AND payment_type_id=(SELECT id FROM payment_types WHERE code='ON_DEBT')), 0) AS debt_sales_today,
            COALESCE((SELECT SUM(amount) FROM expenses WHERE business_id=b.id AND DATE(occurred_at)=CURRENT_DATE AND is_correction=FALSE), 0) AS expenses_today,
            COALESCE((SELECT SUM(amount) FROM customer_payments WHERE business_id=b.id AND DATE(occurred_at)=CURRENT_DATE AND is_correction=FALSE), 0) AS payments_received_today,
            b.cash_on_hand,
            b.total_debt_out
        FROM businesses b
    """)

    op.execute("""
        CREATE VIEW v_debt_priority AS
        SELECT
            c.id AS customer_id,
            c.business_id,
            c.full_name,
            c.phone_number,
            c.debt_balance,
            c.oldest_unpaid_at,
            COALESCE(EXTRACT(DAY FROM NOW() - c.oldest_unpaid_at), 0)::INT AS days_overdue,
            COUNT(dl.id) AS open_invoices,
            (c.debt_balance * COALESCE(EXTRACT(DAY FROM NOW() - c.oldest_unpaid_at), 1)) AS priority_score
        FROM customers c
        LEFT JOIN customer_debt_ledger dl
               ON dl.customer_id = c.id AND dl.status IN ('UNPAID','PARTIAL')
        WHERE c.status = 'ACTIVE' AND c.debt_balance > 0
        GROUP BY c.id, c.business_id, c.full_name, c.phone_number,
                 c.debt_balance, c.oldest_unpaid_at
        ORDER BY priority_score DESC
    """)

    op.execute("""
        CREATE VIEW v_debt_aging AS
        SELECT
            dl.id AS ledger_id,
            dl.business_id,
            dl.customer_id,
            c.full_name AS customer_name,
            dl.sale_id,
            dl.original_amount,
            dl.paid_amount,
            dl.remaining_amount,
            dl.incurred_at,
            dl.due_date,
            dl.status,
            EXTRACT(DAY FROM NOW() - dl.incurred_at)::INT AS age_days,
            CASE
                WHEN EXTRACT(DAY FROM NOW() - dl.incurred_at) <=  7 THEN '0-7 days'
                WHEN EXTRACT(DAY FROM NOW() - dl.incurred_at) <= 14 THEN '8-14 days'
                WHEN EXTRACT(DAY FROM NOW() - dl.incurred_at) <= 30 THEN '15-30 days'
                ELSE 'Over 30 days'
            END AS aging_bucket
        FROM customer_debt_ledger dl
        JOIN customers c ON c.id = dl.customer_id
        WHERE dl.status IN ('UNPAID','PARTIAL')
        ORDER BY dl.incurred_at ASC
    """)

    op.execute("""
        CREATE VIEW v_stock_attention AS
        SELECT
            p.id AS product_id,
            p.business_id,
            pc.name AS category_name,
            p.name,
            p.unit,
            p.stock_on_hand,
            p.low_stock_level,
            p.selling_price,
            p.buying_price,
            p.last_sold_at,
            COALESCE(EXTRACT(DAY FROM NOW() - p.last_sold_at), 999)::INT AS days_since_last_sale,
            CASE
                WHEN p.stock_on_hand <= 0                 THEN 'OUT_OF_STOCK'
                WHEN p.stock_on_hand <= p.low_stock_level THEN 'LOW_STOCK'
                WHEN EXTRACT(DAY FROM NOW() - p.last_sold_at) > 14 THEN 'SLOW_MOVER'
                ELSE 'OK'
            END AS stock_status
        FROM products p
        LEFT JOIN product_categories pc ON pc.id = p.category_id
        WHERE p.status = 'ACTIVE'
    """)

    op.execute("""
        CREATE VIEW v_product_margin_30d AS
        SELECT
            si.product_id,
            si.business_id,
            p.name AS product_name,
            pc.name AS category_name,
            SUM(si.quantity) AS units_sold,
            SUM(si.line_total) AS revenue,
            SUM(si.quantity * si.buying_cost) AS cost_of_goods,
            SUM(si.line_margin) AS gross_margin,
            CASE
                WHEN SUM(si.line_total) > 0
                THEN ROUND(SUM(si.line_margin) / SUM(si.line_total) * 100, 2)
                ELSE NULL
            END AS margin_pct
        FROM sale_items si
        JOIN products p ON p.id = si.product_id
        LEFT JOIN product_categories pc ON pc.id = p.category_id
        JOIN sales s ON s.id = si.sale_id
        WHERE si.buying_cost IS NOT NULL
          AND s.occurred_at >= NOW() - INTERVAL '30 days'
          AND s.is_correction = FALSE
        GROUP BY si.product_id, si.business_id, p.name, pc.name
    """)

    op.execute("""
        CREATE VIEW v_expense_pressure_30d AS
        SELECT
            e.business_id,
            ecg.code AS group_code,
            ecg.label AS group_label,
            ec.label AS category_label,
            SUM(e.amount) AS total_spent,
            COUNT(*) AS transaction_count
        FROM expenses e
        JOIN expense_categories       ec  ON ec.id  = e.category_id
        JOIN expense_category_groups  ecg ON ecg.id = ec.group_id
        WHERE e.occurred_at >= NOW() - INTERVAL '30 days'
          AND e.is_correction = FALSE
        GROUP BY e.business_id, ecg.code, ecg.label, ec.label
        ORDER BY total_spent DESC
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_expense_pressure_30d")
    op.execute("DROP VIEW IF EXISTS v_product_margin_30d")
    op.execute("DROP VIEW IF EXISTS v_stock_attention")
    op.execute("DROP VIEW IF EXISTS v_debt_aging")
    op.execute("DROP VIEW IF EXISTS v_debt_priority")
    op.execute("DROP VIEW IF EXISTS v_daily_summary")
    op.drop_table("corrections")
    op.drop_table("correction_types")
