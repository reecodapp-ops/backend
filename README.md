# DukaMate Backend

A FastAPI + PostgreSQL backend for a decision-support app for small African
retail businesses. Offline-first, multi-tenant via row-level security, with a
trigger-driven aggregate model and a phased Decision Engine.

**Status:** 16 feature modules complete · **173 tests pass on SQLite** · every
module additionally verified end-to-end against real PostgreSQL with RLS active
under a non-superuser role.

## Architecture in one paragraph

The backend is layered strictly — routers → services → repositories → models.
Every transactional module is implemented under **Strategy A**: services insert
raw event rows only, and PostgreSQL triggers maintain cached aggregates
(`cash_on_hand`, `stock_on_hand`, `debt_balance`, `outstanding_amount`,
`customer_debt_ledger`, `payment_allocations`). The one explicit service-owned
aggregate is `products.avg_buying_cost` (no trigger maintains it). Dashboard
endpoints are read-only projections of database views — no analytics in
Python. The Decision Engine reads those same views, applies threshold tests,
and persists `issues` rows that the dashboard renders directly.

## Authentication (email + OTP)

The auth flow is email-first with no phone-based login. End-to-end:

```
register (full_name + email)        → user created, EMAIL_VERIFICATION OTP sent
verify-email (email + code)         → is_email_verified = TRUE
set-password (email + password)     → password set, tokens issued
login (email + password)            → tokens
forgot-password (email)             → PASSWORD_RESET OTP sent (silent if unknown)
reset-password (email + code + pw)  → new password set
resend-otp (email + type)           → re-issue OTP, rate-limited
```

OTPs are 6 digits, valid for 10 minutes, single-use. A resend invalidates any
prior active code so there's always at most one valid OTP per
`(user, verification_type)`. The resend endpoint enforces a 60-second cooldown
to slow down brute force. forgot-password and PASSWORD_RESET resends are silent
on unknown emails — the response is identical whether the email exists or not,
so the endpoints can't be used for account enumeration.

`EmailService` is provider-agnostic: methods like
`send_email_verification(to_address, full_name, code)` build a rendered
`OutgoingEmail` and hand it to a `_EmailSender` strategy. The default sender is
`_SMTPSender` (Gmail-friendly). A `_LogOnlySender` exists for tests/dev
(`EMAIL_LOG_ONLY=true`). Switching to SendGrid, SES, Mailgun, or Resend means
adding one sender class — `AuthService` and the templates do not change. Full
guide in **EMAIL_AUTH_SETUP.md**.

## Modules

| # | Module | Endpoints | Tests |
|---|--------|-----------|-------|
| 1 | Auth | `/auth/register`, `/auth/verify-email`, `/auth/resend-otp`, `/auth/set-password`, `/auth/login`, `/auth/forgot-password`, `/auth/reset-password`, `/auth/refresh`, `/auth/logout`, `/auth/me` | 22 |
| 2 | Business Setup | `/businesses` (POST/PATCH/GET) | 14 |
| 3 | Customers | `/customers` (POST/GET/PATCH/archive) | 11 |
| 4 | Suppliers | `/suppliers` (POST/GET/PATCH) | 7 |
| 5 | Products / Categories | `/products`, `/product-categories` | 15 |
| 6 | Sales | `/sales` (cash and on-debt) | 15 |
| 7 | Customer Payments | `/customer-payments` (oldest-first allocation) | 13 |
| 8 | Expenses | `/expenses`, `/expense-categories` | 13 |
| 9 | Stock Purchases | `/stock-purchases` | 14 |
| 10 | Capital | `/capital-transactions`, `/capital-source-types` | 9 |
| 11 | Dashboard | `/dashboard/today`, `/debt-priority`, `/debt-aging`, `/stock-attention`, `/margins`, `/expense-pressure` | 11 |
| 12 | Corrections | `/corrections` (POST/GET) | 11 |
| 13 | Issues | `/issues` (GET/PATCH), `/issues/recompute` | 9 |
| 14 | Sync | `/sync` | 8 |
| 15 | Workers | CLI entrypoints (issue engine, reconciliation) | — |

Every business-scoped endpoint requires the `X-Business-Id` header. The
backend resolves it through `BusinessContext`, validates ownership, and runs
`SET LOCAL app.current_business_id = :bid` so PostgreSQL RLS applies.

## Layout

```
app/
  routers/          FastAPI routers, one per feature
  schemas/          Pydantic v2 request/response DTOs
  services/         business logic, owns transactions and validation
  repositories/     SQLAlchemy queries; only layer that touches the ORM
  models/           SQLAlchemy ORM models mirroring the schema
  core/             config, security, business context, exceptions, deps
  workers/          Decision Engine + nightly reconciliation entrypoints
alembic/versions/   migrations 0001 .. 0008
tests/              165 tests using pytest + httpx + aiosqlite
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run tests on SQLite (no PG needed)
python -m pytest -q

# Run against PostgreSQL
createdb dukamate
DATABASE_URL=postgresql+asyncpg://you@localhost/dukamate alembic upgrade head
JWT_SECRET_KEY=dev DATABASE_URL=postgresql+asyncpg://you@localhost/dukamate \
  uvicorn app.main:app --reload
```

See `DEPLOYMENT.md` for production deployment.

## Strategy A — trigger-based aggregates

| Event | Trigger does | Service does |
|-------|--------------|--------------|
| Cash sale | `cash_on_hand += total`, `stock -= qty` | computes total/buying_cost, sets receipt_number |
| Debt sale | `total_debt_out += total`, ledger row, customer debt update | same plus customer_id validation |
| Customer payment | oldest-first allocation across ledger, cash/debt updates | inserts only the payment row |
| Expense | `cash_on_hand -= amount` | category validation, unusual-amount warning |
| Stock purchase header | `cash_on_hand -= total` | sets total before insert (trigger reads NEW.total) |
| Stock purchase item | `stock += qty`, `last_restocked_at` | weighted avg_buying_cost recompute (service-owned) |
| Capital | direction lookup → cash +/- | refuses OPENING_BALANCE post-onboarding |
| Correction | trigger skipped (`WHEN is_correction=FALSE`) | flips original, inserts replacement, calls reconcile |

## Dashboard — views, not Python

All six endpoints read from PostgreSQL views: `v_daily_summary`,
`v_debt_priority`, `v_debt_aging`, `v_stock_attention`,
`v_product_margin_30d`, `v_expense_pressure_30d`. No aggregation, bucketing,
priority scoring, or aging logic lives in the service layer. The repository
formats UUIDs per dialect (`_biz_param`: hex on SQLite, hyphenated on
PostgreSQL) because raw `text()` queries don't get SQLAlchemy's type
conversion.

## Decision Engine

`POST /issues/recompute` (and the worker) runs four phase-1 rules over the
views and writes/supersedes `issues`:

- **STOCK_FINISHING**: stock ≤ low_stock_level. ALERT if zero, else WARN.
- **DEBT_OVERDUE**: oldest unpaid > 14 days. ALERT if ≥ 30 days, else WARN.
- **LOW_MARGIN**: 30-day margin_pct < 15%. WARN.
- **EXPENSE_PRESSURE**: 30-day expenses / 30-day revenue > 0.6. WARN.

The engine is **convergent**: a second recompute on the same data produces
zero new issues. When a condition disappears, the active issue is
SUPERSEDED rather than deleted, so the audit trail is preserved. Each issue
carries a `data_snapshot` JSONB so the UI can render the card without
re-querying.

## Sync — offline-first batches

`POST /sync` accepts a batch of client-generated events with stable UUIDs:

```json
{
  "device_id": "...",
  "events": [
    {"client_id": "<uuid>", "event_type": "SALE", "client_created_at": "...",
     "payload": {"payment_type": "CASH", "items": [...]}},
    {"client_id": "<uuid>", "event_type": "EXPENSE", ...}
  ]
}
```

- **Idempotent on client_id**: re-sending the same batch returns three
  duplicates with the same `server_record_id`, no double-charge.
- **Sorted by client_created_at**: the request order is the device's; the
  server applies the timeline order.
- **Per-event savepoints**: one bad event rolls back its own savepoint and
  is reported as REJECTED with the upstream `error_code`; the rest of the
  batch commits. Verified in E2E: a 3-event batch with one bad expense
  charged cash for the good sale (+5,000), the good expense (-2,000), and
  rejected the bad expense — final delta exactly +3,000.
- **Pull-down**: response includes refreshed `cash_on_hand`,
  `total_debt_out`, per-product `stock_on_hand` for products touched in the
  batch, and the latest active issues.

## Workers

Two standalone async CLI entrypoints. They are deliberately **not** scheduled
in-process — running from a separate process gives a clean operational
boundary and avoids tying worker lifetime to API uptime.

```bash
# Decision Engine sweep — every 15 minutes is fine for the MVP.
python -m app.workers.issue_engine            # all businesses
python -m app.workers.issue_engine --business <uuid>

# Nightly reconciliation — call reconcile_business_cash for every business.
python -m app.workers.reconciliation
python -m app.workers.reconciliation --business <uuid>
```

Both workers set `app.current_business_id` before issuing reads, so RLS does
not filter rows away when the worker runs as the same non-superuser role as
the API.

## Real schema bugs caught through E2E

Building each module against real PostgreSQL surfaced four genuine bugs in
the canonical schema. The backend ships a working set:

1. **`fn_after_payment_insert` references `v_ledger.sale_id` not in its
   SELECT** — fixed in migration 0005's documented patch.
2. **`v_daily_summary` cartesian product** — the original view does `LEFT
   JOIN sales × LEFT JOIN expenses × LEFT JOIN customer_payments`, so with 2
   sales + 1 expense + 1 payment the expense and payment SUMs come back
   doubled. Migration 0007 ships a corrected view using scalar subqueries.
3. **UUID dialect mismatch in `text()` bindings** — `PG_UUID(as_uuid=True)`
   stores as 32-char hex on SQLite but hyphenated on PostgreSQL; naked
   `str(uuid)` in a `text()` WHERE silently returns no rows on SQLite.
   Fixed via the `_biz_param` helper in dashboard + issue repositories.
4. **`reconcile_business_cash` is `SECURITY INVOKER` and reads under RLS**
   — the worker must set `app.current_business_id` before calling it, or
   RLS hides every row and the function zeroes cash to just
   `opening_balance`. Fixed in the reconciliation worker.

## Migrations

```
0001_auth_tables             users, devices
0002_business_setup          businesses, lookups, capital_transactions
0003_master_data             customers, suppliers, products, categories
0004_sales                   sales, sale_items, customer_debt_ledger
0005_customer_payments       customer_payments, payment_allocations
0006_money_modules           expenses, stock_purchases, capital_source_types
0007_corrections_dashboard   corrections + 6 dashboard views
0008_issues_sync             issue_types (seeded 9), issues, sync_events
0009_email_auth              users reshape (email-based) + verification_codes
```

## Opening-balance design note

`businesses.opening_balance` is set once during onboarding and recorded as a
single OPENING_BALANCE row in `capital_transactions`. The trigger on capital
insert adds `direction='I'` amounts to `cash_on_hand`, so the cash includes
the opening balance via that row.

A naive correction endpoint that accepted `type_code = "OPENING_BALANCE"`
would let users record OPENING_BALANCE rows post-onboarding, double-counting
them with the `opening_balance` column. The capital endpoint refuses
OPENING_BALANCE via a Pydantic `Literal` (422 at the schema layer) and the
service guards it again. To set the opening balance, you must go through
the business onboarding flow.

## Testing

```bash
python -m pytest -q                          # all 165 tests, ~2min
python -m pytest tests/test_sale.py -q       # one suite
python -m pytest -q -k "correction"          # by keyword
```

SQLite is used for the test suite (no triggers, no views, no RLS). The conftest
creates SQLite-equivalent views with `julianday()` / CASE expressions matching
the PostgreSQL views' column contracts so the dashboard repository's queries
run unchanged. Triggers' effects are verified separately in the per-module E2E
runs against a real PostgreSQL instance.

## Why this architecture

- **Triggers as authoritative ledger updaters**: the most reliable place to
  enforce "every cash movement updates the cached balance" is at the data
  layer. Mixing trigger updates with Python-side updates is how every
  double-counting bug starts.
- **Views as the analytics layer**: aggregations belong where the data lives.
  The dashboard can be ported to any client that speaks SQL.
- **RLS as the security boundary**: a single bug in
  `WHERE business_id = :bid` becomes a tenant-isolation incident in a
  multi-tenant system. Pushing isolation to RLS means even queries that
  forget the filter return no foreign rows.
- **Reconcile as a safety net**: the worker recomputes cash from scratch
  nightly. If a trigger ever drifts, the next run snaps it back.

See `DEPLOYMENT.md` for how to ship this.
