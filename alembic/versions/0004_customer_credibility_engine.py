"""customer credibility engine, sale due_date/receipt_shared_to, ledger risk_flag

Revision ID: 0004_customer_credibility_engine
Revises: 0003_drop_legacy_currency_columns
Create Date: 2026-07-02

WHAT THIS MIGRATION DOES
-------------------------
1. Adds the Customer Credibility Engine columns to ``customers``
   (credit_score, credit_level, is_credit_blocked, recommended_credit_limit,
   average_payment_days, late_payment_count, on_time_payment_count,
   fully_paid_debt_count, last_credit_review) with their CHECK constraints and
   the supporting index (idx_customers_credit). Every existing customer starts
   at credit_level='UNSCORED' (the column default) until the backfill step
   below runs the real scoring function once for each of them.

2. Adds ``customer_debt_ledger.risk_flag`` (BOOLEAN, default FALSE) and its
   partial index (idx_debt_ledger_risk).

3. **Column type change (safe — see note):** changes
   ``customer_debt_ledger.due_date`` from TIMESTAMPTZ to DATE, matching the
   schema. Before this schema version, the application had no way to set this
   column (the ``sales`` table had no ``due_date`` of its own to copy from,
   and no endpoint accepted one), so in a database created from this
   codebase's prior migrations the column is NULL on every existing row —
   casting NULL to NULL is a no-op. The ``USING due_date::date`` clause is
   included defensively in case any row was ever populated by hand or by a
   process outside this codebase; in that case only the time-of-day and
   timezone components of that value are discarded, the date itself is kept.

4. Adds ``sales.due_date`` (DATE, nullable) and ``sales.receipt_shared_to``
   (VARCHAR(20), nullable) plus the ``idx_sales_due_date`` partial index.

5. Seeds the five new ``issue_types`` rows introduced by the Customer
   Credibility Engine (CUSTOMER_HIGH_RISK, CUSTOMER_EXCELLENT,
   CREDIT_LIMIT_REACHED, PAYMENT_OVERDUE, REPEATED_DEFAULT). Uses
   ``ON CONFLICT (code) DO NOTHING`` so it is safe to re-run.

6. Creates/replaces the engine functions and triggers:
     - ``recompute_customer_credit_score(customer_id)``
     - ``recompute_all_credit_scores()`` (nightly, global — see the RLS caveat
       in ``app/workers/credit_engine.py``; this project's worker calls the
       per-customer function per-business instead of this global one, for the
       same SECURITY-INVOKER-under-RLS reason documented for
       ``reconcile_business_cash`` in an earlier migration)
     - ``raise_overdue_debt_issues()`` — **note:** this function is named in
       dukamate_schema_v3.sql's own changelog comment but its body is not
       present anywhere in the schema document we were given to migrate from.
       The version created here is a reconstruction consistent with the
       documented PAYMENT_OVERDUE issue type (raises/resolves a PAYMENT_OVERDUE
       issue per business for each debt ledger row that is UNPAID/PARTIAL and
       past its due_date). Please review this function specifically against
       your actual source-of-truth schema if one exists beyond the document
       used here, since it was not literally copy-paste-able like the others.
     - ``trg_after_sale_insert`` (CREATE OR REPLACE) — updated to copy
       ``NEW.due_date`` onto the created ``customer_debt_ledger`` row and to
       call ``recompute_customer_credit_score`` after an ON_DEBT sale.
     - ``trg_after_payment_insert`` (CREATE OR REPLACE) — updated to maintain
       ``risk_flag`` on ledger rows and call
       ``recompute_customer_credit_score`` after allocation.
   These CREATE OR REPLACE statements are idempotent and safe to re-run.

7. Creates/replaces the views ``v_customer_credit`` (new), ``v_today_activity``
   (new), and ``v_debt_aging`` (replaced — now also selects ``risk_flag``).

8. **Backfill:** calls ``recompute_customer_credit_score(id)`` once for every
   existing ACTIVE customer so credit_level reflects real history immediately
   after migration, rather than waiting for the next sale/payment/nightly run.

WHY THIS IS SAFE
------------------
Steps 1, 2, 4, 5, 6, 7 are purely additive (new columns/rows/functions/views
with safe defaults) or idempotent replacements of function/trigger/view
bodies. Step 3 is a type change but is a no-op for any realistic existing
dataset, as explained above. Step 8 only updates the new credit columns this
migration just added — it cannot corrupt any pre-existing data since those
columns did not exist before this migration.

DOWNGRADE
---------
Drops the two new views, restores the prior ``v_debt_aging`` definition
(without risk_flag), drops the new/replaced functions and reverts the two
trigger functions to their pre-credibility-engine bodies, drops the seeded
issue_types rows (by code), drops ``sales.due_date`` /
``sales.receipt_shared_to``, reverts ``customer_debt_ledger.due_date`` to
TIMESTAMPTZ, and drops ``customer_debt_ledger.risk_flag`` and every
``customers`` credit column. This is destructive for the credit-engine data
specifically (scores/levels computed after upgrade are lost), which is
expected and acceptable for a feature rollback.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_customer_credibility_engine"
down_revision: Union[str, None] = "0003_drop_legacy_currency_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ISSUE_TYPES_SEED = [
    ("CUSTOMER_HIGH_RISK", "Customer credit risk is high", 1, "WARN",
     "Credit score fell into HIGH_RISK or BLOCKED range"),
    ("CUSTOMER_EXCELLENT", "Customer is a trusted payer", 2, "INFO",
     "Credit score reached the EXCELLENT range"),
    ("CREDIT_LIMIT_REACHED", "Customer reached their recommended limit", 1, "WARN",
     "debt_balance >= recommended_credit_limit"),
    ("PAYMENT_OVERDUE", "Customer payment is overdue", 1, "WARN",
     "An open debt ledger entry passed its due date"),
    ("REPEATED_DEFAULT", "Customer has a pattern of late payments", 2, "ALERT",
     "late_payment_count crossed the repeat-offender threshold"),
]

_RECOMPUTE_CREDIT_SCORE_FN = r"""
CREATE OR REPLACE FUNCTION recompute_customer_credit_score(p_customer_id UUID)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_business_id         UUID;
    v_has_history         BOOLEAN;
    v_fully_paid_count    INT;
    v_on_time_count       INT;
    v_late_count          INT;
    v_overdue_open_count  INT;
    v_over_60_count       INT;
    v_over_120_count      INT;
    v_avg_payment_days    NUMERIC;
    v_max_debt_issued     NUMERIC(15,2);
    v_score               INT;
    v_level               VARCHAR(20);
    v_recommended_limit   NUMERIC(15,2);
    v_high_risk_type_id   SMALLINT;
    v_excellent_type_id   SMALLINT;
    v_limit_type_id       SMALLINT;
    v_repeat_type_id      SMALLINT;
BEGIN
    SELECT business_id INTO v_business_id FROM customers WHERE id = p_customer_id;
    IF v_business_id IS NULL THEN
        RETURN;
    END IF;

    SELECT EXISTS (SELECT 1 FROM customer_debt_ledger WHERE customer_id = p_customer_id)
        INTO v_has_history;

    IF NOT v_has_history THEN
        UPDATE customers
        SET credit_level = 'UNSCORED',
            last_credit_review = NOW(),
            updated_at = NOW()
        WHERE id = p_customer_id;
        RETURN;
    END IF;

    SELECT COUNT(*) INTO v_fully_paid_count
    FROM customer_debt_ledger WHERE customer_id = p_customer_id AND status = 'PAID';

    SELECT
        COUNT(*) FILTER (WHERE due_date IS NOT NULL AND fully_paid_at::date <= due_date),
        COUNT(*) FILTER (WHERE due_date IS NOT NULL AND fully_paid_at::date >  due_date)
    INTO v_on_time_count, v_late_count
    FROM customer_debt_ledger
    WHERE customer_id = p_customer_id AND status = 'PAID';

    SELECT COUNT(*) INTO v_overdue_open_count
    FROM customer_debt_ledger
    WHERE customer_id = p_customer_id
      AND status IN ('UNPAID','PARTIAL')
      AND ( (due_date IS NOT NULL AND due_date < CURRENT_DATE)
            OR (due_date IS NULL AND incurred_at < NOW() - INTERVAL '30 days') );

    SELECT COUNT(*) INTO v_over_60_count
    FROM customer_debt_ledger
    WHERE customer_id = p_customer_id AND status IN ('UNPAID','PARTIAL')
      AND incurred_at < NOW() - INTERVAL '60 days';

    SELECT COUNT(*) INTO v_over_120_count
    FROM customer_debt_ledger
    WHERE customer_id = p_customer_id AND status IN ('UNPAID','PARTIAL')
      AND incurred_at < NOW() - INTERVAL '120 days';

    SELECT AVG(EXTRACT(DAY FROM fully_paid_at - incurred_at))
    INTO v_avg_payment_days
    FROM customer_debt_ledger
    WHERE customer_id = p_customer_id AND status = 'PAID';

    v_score := 50
             + (v_fully_paid_count   * 5)
             + (v_on_time_count      * 2)
             - (v_late_count         * 5)
             - (v_overdue_open_count * 10)
             - (v_over_60_count      * 20)
             - (v_over_120_count     * 30);
    v_score := GREATEST(0, LEAST(100, v_score));

    v_level := CASE
        WHEN v_score <= 20 THEN 'BLOCKED'
        WHEN v_score <= 40 THEN 'HIGH_RISK'
        WHEN v_score <= 60 THEN 'AVERAGE'
        WHEN v_score <= 80 THEN 'GOOD'
        ELSE 'EXCELLENT'
    END;

    SELECT COALESCE(total_debt_issued, 0) INTO v_max_debt_issued
    FROM customers WHERE id = p_customer_id;

    v_recommended_limit := ROUND(GREATEST(v_max_debt_issued, 50000) * (v_score / 100.0), 0);

    UPDATE customers
    SET credit_score              = v_score,
        credit_level              = v_level,
        is_credit_blocked         = (v_score <= 20),
        recommended_credit_limit  = v_recommended_limit,
        average_payment_days      = v_avg_payment_days,
        late_payment_count        = v_late_count,
        on_time_payment_count     = v_on_time_count,
        fully_paid_debt_count     = v_fully_paid_count,
        last_credit_review        = NOW(),
        updated_at                = NOW()
    WHERE id = p_customer_id;

    UPDATE customer_debt_ledger
    SET risk_flag = TRUE, updated_at = NOW()
    WHERE customer_id = p_customer_id
      AND status IN ('UNPAID','PARTIAL')
      AND risk_flag = FALSE
      AND ( (due_date IS NOT NULL AND due_date < CURRENT_DATE)
            OR (due_date IS NULL AND incurred_at < NOW() - INTERVAL '30 days') );

    UPDATE customer_debt_ledger
    SET risk_flag = FALSE, updated_at = NOW()
    WHERE customer_id = p_customer_id
      AND risk_flag = TRUE
      AND NOT ( status IN ('UNPAID','PARTIAL')
                AND ( (due_date IS NOT NULL AND due_date < CURRENT_DATE)
                      OR (due_date IS NULL AND incurred_at < NOW() - INTERVAL '30 days') ) );

    SELECT id INTO v_high_risk_type_id FROM issue_types WHERE code = 'CUSTOMER_HIGH_RISK';
    SELECT id INTO v_excellent_type_id FROM issue_types WHERE code = 'CUSTOMER_EXCELLENT';
    SELECT id INTO v_limit_type_id     FROM issue_types WHERE code = 'CREDIT_LIMIT_REACHED';
    SELECT id INTO v_repeat_type_id    FROM issue_types WHERE code = 'REPEATED_DEFAULT';

    IF v_level IN ('HIGH_RISK','BLOCKED') THEN
        INSERT INTO issues (business_id, issue_type_id, ref_customer_id, severity, headline, body_text, suggested_action, data_snapshot)
        VALUES (v_business_id, v_high_risk_type_id, p_customer_id,
                CASE WHEN v_level = 'BLOCKED' THEN 'ALERT' ELSE 'WARN' END,
                'Customer credit risk is high',
                format('Credit score is %s out of 100 (%s).', v_score, v_level),
                'Avoid giving new debt until the outstanding balance is cleared.',
                jsonb_build_object('score', v_score, 'level', v_level))
        ON CONFLICT DO NOTHING;
    ELSE
        UPDATE issues SET status = 'RESOLVED', resolved_at = NOW()
        WHERE ref_customer_id = p_customer_id AND status = 'ACTIVE' AND issue_type_id = v_high_risk_type_id;
    END IF;

    IF v_level = 'EXCELLENT' THEN
        INSERT INTO issues (business_id, issue_type_id, ref_customer_id, severity, headline, body_text, suggested_action, data_snapshot)
        VALUES (v_business_id, v_excellent_type_id, p_customer_id, 'INFO',
                'Customer is a trusted payer',
                format('Credit score is %s out of 100.', v_score),
                'Safe to extend a higher credit limit to this customer.',
                jsonb_build_object('score', v_score, 'level', v_level))
        ON CONFLICT DO NOTHING;
    ELSE
        UPDATE issues SET status = 'RESOLVED', resolved_at = NOW()
        WHERE ref_customer_id = p_customer_id AND status = 'ACTIVE' AND issue_type_id = v_excellent_type_id;
    END IF;

    IF (SELECT debt_balance FROM customers WHERE id = p_customer_id) >= v_recommended_limit
       AND v_recommended_limit > 0 THEN
        INSERT INTO issues (business_id, issue_type_id, ref_customer_id, severity, headline, body_text, suggested_action, data_snapshot)
        VALUES (v_business_id, v_limit_type_id, p_customer_id, 'WARN',
                'Customer reached their recommended credit limit',
                'Current debt balance has reached or passed the recommended limit.',
                'Consider requesting payment before approving further debt.',
                jsonb_build_object('recommended_limit', v_recommended_limit))
        ON CONFLICT DO NOTHING;
    ELSE
        UPDATE issues SET status = 'RESOLVED', resolved_at = NOW()
        WHERE ref_customer_id = p_customer_id AND status = 'ACTIVE' AND issue_type_id = v_limit_type_id;
    END IF;

    IF v_late_count >= 3 THEN
        INSERT INTO issues (business_id, issue_type_id, ref_customer_id, severity, headline, body_text, suggested_action, data_snapshot)
        VALUES (v_business_id, v_repeat_type_id, p_customer_id, 'ALERT',
                'Customer has a pattern of late payments',
                format('%s debts have been paid late.', v_late_count),
                'Consider requiring part-payment upfront on future debt sales.',
                jsonb_build_object('late_payment_count', v_late_count))
        ON CONFLICT DO NOTHING;
    ELSE
        UPDATE issues SET status = 'RESOLVED', resolved_at = NOW()
        WHERE ref_customer_id = p_customer_id AND status = 'ACTIVE' AND issue_type_id = v_repeat_type_id;
    END IF;
END;
$$;
"""

_RECOMPUTE_ALL_CREDIT_SCORES_FN = r"""
CREATE OR REPLACE FUNCTION recompute_all_credit_scores()
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_cust RECORD;
BEGIN
    FOR v_cust IN
        SELECT c.id FROM customers c
        JOIN businesses b ON b.id = c.business_id
        WHERE c.status = 'ACTIVE' AND b.status = 'ACTIVE'
    LOOP
        PERFORM recompute_customer_credit_score(v_cust.id);
    END LOOP;
END;
$$;
"""

# NOTE: reconstructed — see migration docstring point 6.
_RAISE_OVERDUE_DEBT_ISSUES_FN = r"""
CREATE OR REPLACE FUNCTION raise_overdue_debt_issues()
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_row RECORD;
    v_type_id SMALLINT;
BEGIN
    SELECT id INTO v_type_id FROM issue_types WHERE code = 'PAYMENT_OVERDUE';

    FOR v_row IN
        SELECT dl.business_id, dl.customer_id, dl.id AS ledger_id,
               dl.due_date, dl.remaining_amount
        FROM customer_debt_ledger dl
        WHERE dl.status IN ('UNPAID', 'PARTIAL')
          AND dl.due_date IS NOT NULL
          AND dl.due_date < CURRENT_DATE
    LOOP
        INSERT INTO issues (
            business_id, issue_type_id, ref_customer_id, severity,
            headline, body_text, suggested_action, data_snapshot
        )
        VALUES (
            v_row.business_id, v_type_id, v_row.customer_id, 'WARN',
            'Customer payment is overdue',
            format('A debt of %s passed its due date on %s.',
                   v_row.remaining_amount, v_row.due_date),
            'Follow up with the customer for payment.',
            jsonb_build_object('ledger_id', v_row.ledger_id, 'due_date', v_row.due_date)
        )
        ON CONFLICT DO NOTHING;
    END LOOP;

    -- Resolve PAYMENT_OVERDUE issues for customers with no more overdue debt.
    UPDATE issues i
    SET status = 'RESOLVED', resolved_at = NOW()
    WHERE i.status = 'ACTIVE'
      AND i.issue_type_id = v_type_id
      AND NOT EXISTS (
          SELECT 1 FROM customer_debt_ledger dl
          WHERE dl.customer_id = i.ref_customer_id
            AND dl.status IN ('UNPAID','PARTIAL')
            AND dl.due_date IS NOT NULL
            AND dl.due_date < CURRENT_DATE
      );
END;
$$;
"""

_TRG_AFTER_SALE_INSERT_FN = r"""
CREATE OR REPLACE FUNCTION trg_after_sale_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_payment_code VARCHAR(30);
BEGIN
    SELECT code INTO v_payment_code
    FROM payment_types WHERE id = NEW.payment_type_id;

    IF v_payment_code = 'CASH' THEN
        UPDATE businesses
        SET cash_on_hand = cash_on_hand + NEW.total_amount,
            updated_at   = NOW()
        WHERE id = NEW.business_id;

    ELSIF v_payment_code = 'ON_DEBT' THEN
        UPDATE customers
        SET debt_balance        = debt_balance + NEW.total_amount,
            total_debt_issued   = total_debt_issued + NEW.total_amount,
            oldest_unpaid_at    = COALESCE(oldest_unpaid_at, NEW.occurred_at),
            last_transaction_at = NEW.occurred_at,
            updated_at          = NOW()
        WHERE id = NEW.customer_id;

        UPDATE businesses
        SET total_debt_out = total_debt_out + NEW.total_amount,
            updated_at     = NOW()
        WHERE id = NEW.business_id;

        INSERT INTO customer_debt_ledger
            (business_id, customer_id, sale_id, original_amount, incurred_at, due_date, status)
        VALUES
            (NEW.business_id, NEW.customer_id, NEW.id, NEW.total_amount, NEW.occurred_at, NEW.due_date, 'UNPAID');

        UPDATE sales SET outstanding_amount = NEW.total_amount WHERE id = NEW.id;

        PERFORM recompute_customer_credit_score(NEW.customer_id);
    END IF;

    RETURN NEW;
END;
$$;
"""

_TRG_AFTER_PAYMENT_INSERT_FN = r"""
CREATE OR REPLACE FUNCTION trg_after_payment_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_remaining     NUMERIC(15,2) := NEW.amount;
    v_ledger        RECORD;
    v_allocate      NUMERIC(15,2);
BEGIN
    UPDATE customers
    SET debt_balance        = GREATEST(0, debt_balance - NEW.amount),
        total_paid          = total_paid + NEW.amount,
        last_transaction_at = NEW.occurred_at,
        updated_at          = NOW()
    WHERE id = NEW.customer_id;

    UPDATE businesses
    SET cash_on_hand   = cash_on_hand + NEW.amount,
        total_debt_out = GREATEST(0, total_debt_out - NEW.amount),
        updated_at     = NOW()
    WHERE id = NEW.business_id;

    FOR v_ledger IN
        SELECT id, sale_id, remaining_amount
        FROM customer_debt_ledger
        WHERE customer_id = NEW.customer_id
          AND business_id = NEW.business_id
          AND status IN ('UNPAID', 'PARTIAL')
        ORDER BY incurred_at ASC
    LOOP
        EXIT WHEN v_remaining <= 0;

        v_allocate := LEAST(v_remaining, v_ledger.remaining_amount);

        INSERT INTO payment_allocations
            (business_id, payment_id, debt_ledger_id, allocated_amount)
        VALUES
            (NEW.business_id, NEW.id, v_ledger.id, v_allocate);

        UPDATE customer_debt_ledger
        SET paid_amount  = paid_amount + v_allocate,
            status       = CASE
                               WHEN (paid_amount + v_allocate) >= original_amount
                               THEN 'PAID'
                               ELSE 'PARTIAL'
                           END,
            fully_paid_at = CASE
                                WHEN (paid_amount + v_allocate) >= original_amount
                                THEN NOW()
                                ELSE NULL
                            END,
            risk_flag    = CASE
                                WHEN (paid_amount + v_allocate) >= original_amount
                                THEN FALSE
                                ELSE risk_flag
                           END,
            updated_at   = NOW()
        WHERE id = v_ledger.id;

        UPDATE sales
        SET outstanding_amount = GREATEST(0, outstanding_amount - v_allocate),
            updated_at         = NOW()
        WHERE id = v_ledger.sale_id;

        v_remaining := v_remaining - v_allocate;
    END LOOP;

    UPDATE customers
    SET oldest_unpaid_at = (
            SELECT MIN(incurred_at)
            FROM customer_debt_ledger
            WHERE customer_id = NEW.customer_id
              AND business_id  = NEW.business_id
              AND status IN ('UNPAID','PARTIAL')
        ),
        updated_at = NOW()
    WHERE id = NEW.customer_id;

    PERFORM recompute_customer_credit_score(NEW.customer_id);

    RETURN NEW;
END;
$$;
"""

_V_CUSTOMER_CREDIT_VIEW = r"""
CREATE OR REPLACE VIEW v_customer_credit AS
SELECT
    c.id                        AS customer_id,
    c.business_id,
    c.full_name,
    c.phone_number,
    c.credit_score,
    c.credit_level,
    c.is_credit_blocked,
    c.debt_balance,
    c.recommended_credit_limit,
    GREATEST(c.recommended_credit_limit - c.debt_balance, 0) AS available_credit,
    c.average_payment_days,
    c.on_time_payment_count,
    c.late_payment_count,
    c.fully_paid_debt_count,
    COALESCE(EXTRACT(DAY FROM NOW() - c.oldest_unpaid_at), 0)::INT AS days_overdue,
    c.last_credit_review,
    CASE
        WHEN c.credit_level = 'UNSCORED'  THEN 'Not enough history yet — treat as a new customer.'
        WHEN c.is_credit_blocked          THEN 'Avoid giving new debt.'
        WHEN c.credit_level = 'HIGH_RISK' THEN 'Give debt with caution; consider part-payment upfront.'
        WHEN c.credit_level = 'AVERAGE'   THEN 'Safe for small debt amounts, within the recommended limit.'
        WHEN c.credit_level = 'GOOD'      THEN 'Safe to extend debt within the recommended limit.'
        WHEN c.credit_level = 'EXCELLENT' THEN 'Trusted customer; safe for a higher debt amount.'
    END AS recommendation
FROM customers c
WHERE c.status = 'ACTIVE';
"""

_V_TODAY_ACTIVITY_VIEW = r"""
CREATE OR REPLACE VIEW v_today_activity AS
SELECT business_id, 'SALE'::VARCHAR(20) AS activity_type, id AS record_id,
       total_amount AS amount, occurred_at
FROM sales WHERE is_correction = FALSE
UNION ALL
SELECT business_id, 'EXPENSE', id, amount, occurred_at
FROM expenses WHERE is_correction = FALSE
UNION ALL
SELECT business_id, 'STOCK_PURCHASE', id, total_buying_cost, occurred_at
FROM stock_purchases WHERE is_correction = FALSE
UNION ALL
SELECT business_id, 'CAPITAL_TRANSACTION', id, amount, occurred_at
FROM capital_transactions WHERE is_correction = FALSE
UNION ALL
SELECT business_id, 'CUSTOMER_PAYMENT', id, amount, occurred_at
FROM customer_payments WHERE is_correction = FALSE;
"""

_V_DEBT_AGING_VIEW_WITH_RISK = r"""
CREATE OR REPLACE VIEW v_debt_aging AS
SELECT
    dl.id               AS ledger_id,
    dl.business_id,
    dl.customer_id,
    c.full_name         AS customer_name,
    dl.sale_id,
    dl.original_amount,
    dl.paid_amount,
    dl.remaining_amount,
    dl.incurred_at,
    dl.due_date,
    dl.status,
    dl.risk_flag,
    EXTRACT(DAY FROM NOW() - dl.incurred_at)::INT AS age_days,
    CASE
        WHEN EXTRACT(DAY FROM NOW() - dl.incurred_at) <= 7   THEN '0-7 days'
        WHEN EXTRACT(DAY FROM NOW() - dl.incurred_at) <= 14  THEN '8-14 days'
        WHEN EXTRACT(DAY FROM NOW() - dl.incurred_at) <= 30  THEN '15-30 days'
        ELSE 'Over 30 days'
    END AS aging_bucket
FROM customer_debt_ledger dl
JOIN customers c ON c.id = dl.customer_id
WHERE dl.status IN ('UNPAID','PARTIAL')
ORDER BY dl.incurred_at ASC;
"""

_V_DEBT_AGING_VIEW_WITHOUT_RISK = r"""
CREATE OR REPLACE VIEW v_debt_aging AS
SELECT
    dl.id               AS ledger_id,
    dl.business_id,
    dl.customer_id,
    c.full_name         AS customer_name,
    dl.sale_id,
    dl.original_amount,
    dl.paid_amount,
    dl.remaining_amount,
    dl.incurred_at,
    dl.due_date,
    dl.status,
    EXTRACT(DAY FROM NOW() - dl.incurred_at)::INT AS age_days,
    CASE
        WHEN EXTRACT(DAY FROM NOW() - dl.incurred_at) <= 7   THEN '0-7 days'
        WHEN EXTRACT(DAY FROM NOW() - dl.incurred_at) <= 14  THEN '8-14 days'
        WHEN EXTRACT(DAY FROM NOW() - dl.incurred_at) <= 30  THEN '15-30 days'
        ELSE 'Over 30 days'
    END AS aging_bucket
FROM customer_debt_ledger dl
JOIN customers c ON c.id = dl.customer_id
WHERE dl.status IN ('UNPAID','PARTIAL')
ORDER BY dl.incurred_at ASC;
"""


def upgrade() -> None:
    # ----- 1. customers: credit engine columns --------------------------------
    op.add_column("customers", sa.Column("credit_score", sa.SmallInteger(), nullable=True))
    op.add_column(
        "customers",
        sa.Column(
            "credit_level", sa.String(20), nullable=False, server_default="UNSCORED"
        ),
    )
    op.add_column(
        "customers",
        sa.Column("is_credit_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "customers",
        sa.Column(
            "recommended_credit_limit",
            sa.Numeric(15, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("customers", sa.Column("average_payment_days", sa.Numeric(6, 1), nullable=True))
    op.add_column(
        "customers",
        sa.Column("late_payment_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "customers",
        sa.Column("on_time_payment_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "customers",
        sa.Column("fully_paid_debt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "customers", sa.Column("last_credit_review", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "customers_credit_level_check",
        "customers",
        "credit_level IN ('UNSCORED','BLOCKED','HIGH_RISK','AVERAGE','GOOD','EXCELLENT')",
    )
    op.create_check_constraint(
        "customers_credit_score_check",
        "customers",
        "credit_score IS NULL OR credit_score BETWEEN 0 AND 100",
    )
    op.create_index("idx_customers_credit", "customers", ["business_id", "credit_level"])

    # ----- 2. customer_debt_ledger: risk_flag + due_date type fix -------------
    op.add_column(
        "customer_debt_ledger",
        sa.Column("risk_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "idx_debt_ledger_risk",
        "customer_debt_ledger",
        ["business_id", "risk_flag"],
        postgresql_where=sa.text("risk_flag = TRUE"),
    )
    op.alter_column(
        "customer_debt_ledger",
        "due_date",
        type_=sa.Date(),
        postgresql_using="due_date::date",
    )

    # ----- 3. sales: due_date + receipt_shared_to -----------------------------
    op.add_column("sales", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("sales", sa.Column("receipt_shared_to", sa.String(20), nullable=True))
    op.create_index(
        "idx_sales_due_date",
        "sales",
        ["business_id", "due_date"],
        postgresql_where=sa.text("due_date IS NOT NULL AND outstanding_amount > 0"),
    )

    # ----- 4. issue_types seed -------------------------------------------------
    issue_types_tbl = sa.table(
        "issue_types",
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("engine_phase", sa.SmallInteger),
        sa.column("severity_default", sa.String),
        sa.column("description", sa.Text),
    )
    for code, label, phase, severity, description in _ISSUE_TYPES_SEED:
        op.execute(
            sa.text(
                """
                INSERT INTO issue_types (code, label, engine_phase, severity_default, description)
                VALUES (:code, :label, :phase, :severity, :description)
                ON CONFLICT (code) DO NOTHING
                """
            ).bindparams(
                code=code, label=label, phase=phase, severity=severity, description=description
            )
        )

    # ----- 5. functions / triggers / views ------------------------------------
    op.execute(sa.text(_RECOMPUTE_CREDIT_SCORE_FN))
    op.execute(sa.text(_RECOMPUTE_ALL_CREDIT_SCORES_FN))
    op.execute(sa.text(_RAISE_OVERDUE_DEBT_ISSUES_FN))
    op.execute(sa.text(_TRG_AFTER_SALE_INSERT_FN))
    op.execute(sa.text(_TRG_AFTER_PAYMENT_INSERT_FN))
    op.execute(sa.text(_V_CUSTOMER_CREDIT_VIEW))
    op.execute(sa.text(_V_TODAY_ACTIVITY_VIEW))
    op.execute(sa.text(_V_DEBT_AGING_VIEW_WITH_RISK))

    # ----- 6. backfill: score every existing active customer once ------------
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE v_cust RECORD;
            BEGIN
                FOR v_cust IN SELECT id FROM customers WHERE status = 'ACTIVE' LOOP
                    PERFORM recompute_customer_credit_score(v_cust.id);
                END LOOP;
            END $$;
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text(_V_DEBT_AGING_VIEW_WITHOUT_RISK))
    op.execute(sa.text("DROP VIEW IF EXISTS v_today_activity"))
    op.execute(sa.text("DROP VIEW IF EXISTS v_customer_credit"))

    # Restore the pre-credibility-engine trigger bodies (no due_date copy, no
    # credit-score calls, no risk_flag maintenance).
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION trg_after_sale_insert()
            RETURNS TRIGGER LANGUAGE plpgsql AS $$
            DECLARE
                v_payment_code VARCHAR(30);
            BEGIN
                SELECT code INTO v_payment_code
                FROM payment_types WHERE id = NEW.payment_type_id;

                IF v_payment_code = 'CASH' THEN
                    UPDATE businesses
                    SET cash_on_hand = cash_on_hand + NEW.total_amount, updated_at = NOW()
                    WHERE id = NEW.business_id;
                ELSIF v_payment_code = 'ON_DEBT' THEN
                    UPDATE customers
                    SET debt_balance        = debt_balance + NEW.total_amount,
                        total_debt_issued   = total_debt_issued + NEW.total_amount,
                        oldest_unpaid_at    = COALESCE(oldest_unpaid_at, NEW.occurred_at),
                        last_transaction_at = NEW.occurred_at,
                        updated_at          = NOW()
                    WHERE id = NEW.customer_id;

                    UPDATE businesses
                    SET total_debt_out = total_debt_out + NEW.total_amount, updated_at = NOW()
                    WHERE id = NEW.business_id;

                    INSERT INTO customer_debt_ledger
                        (business_id, customer_id, sale_id, original_amount, incurred_at, status)
                    VALUES
                        (NEW.business_id, NEW.customer_id, NEW.id, NEW.total_amount, NEW.occurred_at, 'UNPAID');

                    UPDATE sales SET outstanding_amount = NEW.total_amount WHERE id = NEW.id;
                END IF;
                RETURN NEW;
            END;
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION trg_after_payment_insert()
            RETURNS TRIGGER LANGUAGE plpgsql AS $$
            DECLARE
                v_remaining     NUMERIC(15,2) := NEW.amount;
                v_ledger        RECORD;
                v_allocate      NUMERIC(15,2);
            BEGIN
                UPDATE customers
                SET debt_balance        = GREATEST(0, debt_balance - NEW.amount),
                    total_paid          = total_paid + NEW.amount,
                    last_transaction_at = NEW.occurred_at,
                    updated_at          = NOW()
                WHERE id = NEW.customer_id;

                UPDATE businesses
                SET cash_on_hand   = cash_on_hand + NEW.amount,
                    total_debt_out = GREATEST(0, total_debt_out - NEW.amount),
                    updated_at     = NOW()
                WHERE id = NEW.business_id;

                FOR v_ledger IN
                    SELECT id, sale_id, remaining_amount
                    FROM customer_debt_ledger
                    WHERE customer_id = NEW.customer_id AND business_id = NEW.business_id
                      AND status IN ('UNPAID', 'PARTIAL')
                    ORDER BY incurred_at ASC
                LOOP
                    EXIT WHEN v_remaining <= 0;
                    v_allocate := LEAST(v_remaining, v_ledger.remaining_amount);

                    INSERT INTO payment_allocations
                        (business_id, payment_id, debt_ledger_id, allocated_amount)
                    VALUES (NEW.business_id, NEW.id, v_ledger.id, v_allocate);

                    UPDATE customer_debt_ledger
                    SET paid_amount  = paid_amount + v_allocate,
                        status       = CASE WHEN (paid_amount + v_allocate) >= original_amount THEN 'PAID' ELSE 'PARTIAL' END,
                        fully_paid_at = CASE WHEN (paid_amount + v_allocate) >= original_amount THEN NOW() ELSE NULL END,
                        updated_at   = NOW()
                    WHERE id = v_ledger.id;

                    UPDATE sales
                    SET outstanding_amount = GREATEST(0, outstanding_amount - v_allocate), updated_at = NOW()
                    WHERE id = v_ledger.sale_id;

                    v_remaining := v_remaining - v_allocate;
                END LOOP;

                UPDATE customers
                SET oldest_unpaid_at = (
                        SELECT MIN(incurred_at) FROM customer_debt_ledger
                        WHERE customer_id = NEW.customer_id AND business_id = NEW.business_id
                          AND status IN ('UNPAID','PARTIAL')
                    ),
                    updated_at = NOW()
                WHERE id = NEW.customer_id;

                RETURN NEW;
            END;
            $$;
            """
        )
    )

    op.execute(sa.text("DROP FUNCTION IF EXISTS raise_overdue_debt_issues()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS recompute_all_credit_scores()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS recompute_customer_credit_score(UUID)"))

    for code, *_ in _ISSUE_TYPES_SEED:
        op.execute(
            sa.text("DELETE FROM issue_types WHERE code = :code").bindparams(code=code)
        )

    op.drop_index("idx_sales_due_date", table_name="sales")
    op.drop_column("sales", "receipt_shared_to")
    op.drop_column("sales", "due_date")

    op.alter_column(
        "customer_debt_ledger",
        "due_date",
        type_=sa.DateTime(timezone=True),
        postgresql_using="due_date::timestamptz",
    )
    op.drop_index("idx_debt_ledger_risk", table_name="customer_debt_ledger")
    op.drop_column("customer_debt_ledger", "risk_flag")

    op.drop_index("idx_customers_credit", table_name="customers")
    op.drop_constraint("customers_credit_score_check", "customers", type_="check")
    op.drop_constraint("customers_credit_level_check", "customers", type_="check")
    op.drop_column("customers", "last_credit_review")
    op.drop_column("customers", "fully_paid_debt_count")
    op.drop_column("customers", "on_time_payment_count")
    op.drop_column("customers", "late_payment_count")
    op.drop_column("customers", "average_payment_days")
    op.drop_column("customers", "recommended_credit_limit")
    op.drop_column("customers", "is_credit_blocked")
    op.drop_column("customers", "credit_level")
    op.drop_column("customers", "credit_score")
