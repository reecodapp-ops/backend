# DukaMate backend — v3 schema alignment notes

This document explains what changed, why, in what order to deploy, and which
step (if any) is destructive. Read this before running anything against
production.

## Deployment order

```
alembic upgrade 0001_fix_uuid_generate_v7          # safe
alembic upgrade 0002_add_currencies_and_location   # safe (additive)
#   --- deploy the new application code now, verify in production ---
alembic upgrade 0003_drop_legacy_currency_columns  # DESTRUCTIVE — see below
alembic upgrade 0004_customer_credibility_engine   # safe (additive)
```

`0001` and `0002` can be applied together with the code deploy. **Do not run
`0003` until you have verified `0002`'s backfill is correct in production**
(see that migration's docstring for the exact verification query). `0004` has
no ordering dependency on `0003` other than the linear chain; it could in
principle be applied before `0003` if you reorder the revision graph, but as
shipped it comes after.

### ⚠️ Only genuinely destructive step: `0003_drop_legacy_currency_columns`

Drops `businesses.currency_code`, `businesses.currency_symbol`,
`subscription_plans.currency_code`. The application already stops reading/
writing these columns as of the code in this delivery (values are derived
dynamically from `currency_id` → `currencies`), so this migration is about
reclaiming the columns, not about functionality. It is safe to defer
indefinitely — leaving the dead columns in place costs nothing. Full
data-loss discussion and a verification query are in the migration's
docstring.

Every other migration is additive or an idempotent function/trigger/view
replacement.

## Why no Alembic setup existed to build on

The delivered project (`app/` only) did not include an `alembic/` directory,
`alembic.ini`, or any existing revision history, despite the stack using
SQLAlchemy 2.0 + Alembic. This delivery adds a working Alembic scaffold
(`alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`) wired to the
app's own `Settings.database_url`, with `0001_fix_uuid_generate_v7` as the
new root revision (`down_revision = None`).

**If your production database already has its own Alembic history from a
prior session**, you must rebase: set `down_revision` on
`0001_fix_uuid_generate_v7` to your actual current head revision id instead
of `None`, rather than running `alembic upgrade head` against a database
that already contains these tables (which would fail on `CREATE TABLE
currencies` etc. colliding with nothing, but more importantly on
`ALTER TABLE ... ADD COLUMN` where the underlying schema doesn't yet
structurally match what `0002`/`0004` assume, e.g. if `products.opening_stock_qty`
or other prior deltas already exist under different migration bookkeeping).
Check with your team before running anything here against a database that
isn't fresh.

## Per-migration summary

| Migration | Type | Summary |
|---|---|---|
| `0001_fix_uuid_generate_v7` | Safe | Recreates `uuid_generate_v7()` with the corrected (even-nibble) implementation from the v3 schema; changes every UUID PK column's `DEFAULT` from `gen_random_uuid()` to `uuid_generate_v7()`. No existing id values are touched. |
| `0002_add_currencies_and_location` | Safe (additive) | Creates & seeds `currencies` (ISO 4217, DukaMate markets + majors); adds `businesses.currency_id` (backfilled from `currency_code`, then NOT NULL + FK); adds structured location columns to `businesses` (legacy `location` untouched); adds `subscription_plans.currency_id` (backfilled, nullable). |
| `0003_drop_legacy_currency_columns` | **Destructive** | Drops the three now-unused free-text currency columns. See warning above. |
| `0004_customer_credibility_engine` | Safe (additive) + 1 flagged type change | Adds all Customer Credibility Engine columns to `customers`; adds `customer_debt_ledger.risk_flag`; changes `customer_debt_ledger.due_date` TIMESTAMPTZ→DATE (safe — see migration docstring, this column was never populated by the application before this release); adds `sales.due_date` / `sales.receipt_shared_to`; seeds 5 new `issue_types`; creates/replaces `recompute_customer_credit_score`, `recompute_all_credit_scores`, `raise_overdue_debt_issues` (reconstructed — see below), and replaces `trg_after_sale_insert` / `trg_after_payment_insert`; creates `v_customer_credit`, `v_today_activity`; replaces `v_debt_aging` to include `risk_flag`; backfills every existing active customer's score once. |

### Note on `raise_overdue_debt_issues()`

The v3 schema's own changelog header mentions this function, but its body is
not present anywhere in the schema document this delivery was migrated
from — only `recompute_customer_credit_score`, `recompute_all_credit_scores`,
`reconcile_business_cash`, and `reconcile_all_businesses` have bodies in that
document. `0004` includes a reconstructed version consistent with the
documented `PAYMENT_OVERDUE` issue type (raises/resolves one `PAYMENT_OVERDUE`
issue per overdue ledger row, per business). **Please review this function
specifically** if you have the real source schema available — it was not a
literal copy the way every other function/trigger/view in this delivery is.

### RLS + `SECURITY INVOKER` caveat (same bug class as `reconcile_business_cash`)

`recompute_customer_credit_score` / `recompute_all_credit_scores` are
`SECURITY INVOKER` and read from RLS-protected tables
(`customer_debt_ledger`, `customers`). Calling `recompute_all_credit_scores()`
directly from a session with no `app.current_business_id` set will silently
see zero rows per business under RLS and mis-score everyone as `UNSCORED` —
the exact bug class documented for `reconcile_business_cash` in this
project's history. For that reason, `app/workers/credit_engine.py` does
**not** call the global `recompute_all_credit_scores()`; it loops per
business, sets `app.current_business_id`, then calls
`recompute_customer_credit_score(customer_id)` per customer — mirroring
`app/workers/reconciliation.py`'s existing pattern. Schedule it after
reconciliation:

```
30 3 * * *   python -m app.workers.credit_engine
```

## Application-layer changes (non-Alembic)

- **Models**: `Currency` (new), `Business` (currency_id FK + `currency`
  relationship + backward-compatible `currency_code`/`currency_symbol`
  properties + structured location columns), `SubscriptionPlan`
  (`currency_id` FK replaces `currency_code`), `Customer` (full Customer
  Credibility Engine column set, read-only from the app's perspective),
  `Sale` (`due_date`, `receipt_shared_to`), `CustomerDebtLedger` (`due_date`
  now `Date` not `DateTime`, `risk_flag`), `uuid_pk()` base helper
  (`uuid_generate_v7()` server default).
- **Schemas**: `CurrencyOut`; `BusinessCreateRequest`/`BusinessUpdateRequest`/
  `BusinessOut` reworked for `currency_id` + structured location (no more
  regex-validated free-text `currency_code`); `CustomerOut` +
  new `CustomerCreditOut` (projects `v_customer_credit`, with an SQLite/no-view
  fallback computed from `Customer` columns directly); `SaleCreateRequest`/
  `SaleOut` gain `due_date`/`receipt_shared_to`, new
  `SaleReceiptShareRequest`; `DebtAgingRow` gains `risk_flag`; new
  `TodayActivityRow`/`TodayActivityListOut` (projects `v_today_activity`).
- **Repositories**: `BusinessRepository` gains currency lookups
  (`get_currency`, `get_currency_by_iso_code`, `list_active_currencies`) and
  eager-loads `Business.currency`; `CustomerRepository` gains `get_credit`
  (raw `v_customer_credit` read, PostgreSQL-only — mirrors
  `DashboardRepository`'s pattern); `SaleRepository.insert_sale` takes
  `due_date`, plus a new `set_receipt_shared`; `DashboardRepository` gains
  `get_today_activity`.
- **Services**: `BusinessService` validates `currency_id` against the
  `currencies` table instead of a regex; `CustomerService.get_credit`;
  `SaleService` threads `due_date` through creation and adds
  `share_receipt`; `DashboardService.get_today_activity`.
- **Routers**: new `GET /currencies` (dynamic currency lookup, any
  authenticated user, no business context required — used for onboarding/
  settings pickers); `GET /customers/{id}/credit`; `POST
  /sales/{id}/share-receipt`; `GET /dashboard/today-activity`.
- **Workers**: new `app/workers/credit_engine.py` (nightly credit-score
  recompute, RLS-safe — see above).

## Flagship feature: Customer Credit Intelligence (this delivery)

This delivery wires the FastAPI backend up to the Customer Credibility Engine
data that the schema (and prior migrations in this document) already
persists. No new tables or migrations were needed — this was a pure
application-layer build on top of `customers`, `customer_debt_ledger`,
`customer_payments`, `payment_allocations`, and `issues`.

### Design decisions worth knowing about

- **No credibility math in Python, anywhere.** The score itself
  (`credit_score`/`credit_level`/`is_credit_blocked`/`recommended_credit_limit`/
  the payment-behaviour counters) is written exclusively by
  `recompute_customer_credit_score()` in the database, fired by triggers on
  every debt sale and payment, and nightly by `app/workers/credit_engine.py`.
  Everything in `CustomerService` reads those already-computed columns and
  narrates/projects them — it never recalculates a score.

- **"Score history" is the Decision Engine's own issue trail, not a new
  table.** The schema has no `customer_credit_history` table — only the
  *current* score is persisted. Rather than add one, `GET
  /customers/credit-history` reads the `issues` rows the database itself
  already raises on every level-changing event (`CUSTOMER_HIGH_RISK`,
  `CUSTOMER_EXCELLENT`, `CREDIT_LIMIT_REACHED`, `REPEATED_DEFAULT`,
  `PAYMENT_OVERDUE`), each timestamped with a `data_snapshot` of the score at
  that moment. This is a durable, already-existing audit trail — reusing it
  avoids a schema change and keeps the "score history" honest (it's exactly
  the moments the engine considered significant, not a noisy snapshot of
  every nightly recompute).

- **`reason` / `risk_indicators` are computed for free.** They're built in
  `CustomerService._derive_reason_and_risk` purely from columns already
  loaded on the `Customer` row (no extra query), so list endpoints
  (`/customers/high-risk`, `/customers/recommendation`, etc.) don't pay an
  N+1 penalty to explain every row.

- **Debt-sale validation is a two-tier gate, per the spec.** In
  `SaleService.create_sale`: customer must exist / be `ACTIVE` / not be
  `is_credit_blocked` are **hard** validation failures (422). Exceeding
  `recommended_credit_limit` is a **soft** warning
  (`CREDIT_LIMIT_EXCEEDED`) attached to the response — the sale still goes
  through, and the frontend decides. `CustomerService.simulate_credit`
  mirrors this exact logic as a side-effect-free what-if, so a frontend can
  ask "would this be approved?" before committing to the sale.

- **Route ordering in `app/routers/customer.py`.** FastAPI/Starlette matches
  path templates in registration order; `/customers/high-risk`,
  `/customers/excellent`, `/customers/recommendation`, and
  `/customers/credit-history` are literal-segment routes registered *before*
  `/customers/{customer_id}` so they're never captured as an invalid UUID
  path parameter.

### New endpoints

| Endpoint | Purpose |
|---|---|
| `GET /customers/{id}/credit` | Full credit profile: score, level, limit, outstanding balance, counters, oldest unpaid debt, recommendation, reason, risk indicators |
| `GET /customers/{id}/ledger` | Per-sale debt history (optionally filtered by status) |
| `GET /customers/{id}/payments` | Payment history, each payment paired with its allocation(s) |
| `GET /customers/high-risk` | Credit board filtered to `HIGH_RISK`, riskiest first |
| `GET /customers/excellent` | Credit board filtered to `EXCELLENT`, best first |
| `GET /customers/recommendation` | Full filterable credit board (see filters below) |
| `GET /customers/credit-history` | Decision Engine credit-event timeline, business-wide or `?customer_id=` scoped |
| `POST /customers/{id}/simulate-credit` | "Would a debt sale of X be approved right now?" — pure projection |

### Extended endpoint

`GET /customers` (and the credit board endpoints) now accept: `credit_level`,
`is_blocked`, `has_active_debt`, `min_debt_balance`, `max_debt_balance`,
`overdue` (has a `risk_flag=true` open ledger row), `recently_paid_days` (paid
within the last N days). All additive/optional — existing calls are
unaffected.


- `location` (free-text) on `businesses` is fully preserved, per the request
  to keep it and not introduce new address structures beyond what the new
  schema itself already defines.
- The Phase 1 Issue Engine (`STOCK_FINISHING`, `DEBT_OVERDUE`, `LOW_MARGIN`,
  `EXPENSE_PRESSURE`) in `app/services/issue_service.py` /
  `app/workers/issue_engine.py` is untouched — it does not reference currency
  or credit fields and needed no changes. The five new Customer Credibility
  Engine issue types are raised entirely by the database function, not by
  this Python engine.
- Row-Level Security policies, cascade rules, and every other FK's
  `ondelete` behaviour are unchanged; the new FKs added here
  (`businesses.currency_id`, `subscription_plans.currency_id`) intentionally
  have **no** `ondelete` (default `RESTRICT`-like behaviour via a plain FK) —
  currencies are a stable, admin-managed lookup table and should never be
  deletable out from under a business that references it. If you need to
  deactivate a currency, set `currencies.is_active = false` rather than
  deleting the row.

## Session 3: remaining business features, receipts, unified activity, and tests

This delivery filled in the CRUD/pagination/sorting/search/filtering gaps
across every module, built out the Receipts feature set, generalized the
activity feed, extended the Today dashboard, and added a full pytest suite
(90 tests, all passing). No schema changes were needed or made — everything
here is application-layer, consistent with "the database has already been
migrated."

### Per-module additions

- **Products**: filters for stock_status (OUT_OF_STOCK/LOW_STOCK/OK),
  min_price/max_price, supplier_id (via an EXISTS join through
  stock_purchase_items -> stock_purchases), and order (name/stock/price/recent).
- **Suppliers**: added order and an explicit POST /{id}/archive.
- **Sales**: added customer_id, payment_type (CASH/ON_DEBT), receipt_number
  (partial match) filters, and order (recent/oldest/amount_asc/amount_desc).
- **Expenses**: added GET /expenses/{id}, search (note/supplier_name),
  min_amount/max_amount, and order.
- **Stock Purchases**: added GET /stock-purchases/{id}, search
  (supplier_name_free/note), and order.
- **Capital**: added GET /capital-transactions/{id} and made OPENING_BALANCE
  a valid list filter value (synthesized, see below).
- **Issues**: added customer_id, product_id, and a resolved convenience flag.
- **Subscriptions**: added GET /subscription-plans, GET
  /businesses/{id}/subscriptions (history), and POST
  /businesses/{id}/subscription (switch plans, preserving history).

### Capital opening-balance synthesis

OPENING_BALANCE capital transactions are deliberately never inserted as real
rows (see the double-count rationale in capital_service.py). CapitalService
synthesizes a single read-only entry from businesses.opening_balance /
opening_date on every list/detail read, with a deterministic uuid5 id, so
GET /capital-transactions/{id} also works for it. Presentation-layer only.

### Receipts

- GET /sales/{id}/receipt - preview (no state change).
- POST /sales/{id}/receipt/regenerate - re-issues receipt_number only.
- POST /sales/{id}/share-receipt - now also accepts an optional
  destination_number and returns a wa.me click-to-chat URL
  (whatsapp_share_url). The destination number is NOT persisted - no column
  exists for it; a real per-share history needs a migration.
- Receipt footer: built from the business's own profile (shop name, phone,
  location) plus a fixed thank-you line - no per-business custom-footer
  column exists in the schema.
- Receipt correction: served by the existing generic Corrections module
  (POST /corrections, correction_type_code=SALE_EDIT) rather than a
  duplicated endpoint.

### Unified activity feed - rebuilt to be portable and paginated

GET /dashboard/today-activity read the PostgreSQL-only v_today_activity view
with a hardcoded date_trunc('day', now()) filter and no pagination. Replaced
by GET /dashboard/activity, which builds the same UNION ALL directly in
SQLAlchemy Core across sales/expenses/stock_purchases/capital_transactions/
customer_payments. Portable (SQLite + PostgreSQL), supports
period=today|yesterday|custom, real limit/offset.

### Today dashboard - two metrics the view doesn't carry

DailySummaryOut now also returns stock_purchases_today and profit_today,
computed directly from stock_purchases.total_buying_cost and
sales.total_amount - sales.total_buying_cost (not the generated gross_margin
column, for cross-dialect safety).

### Issues: resolved filter precedence

resolved=true/false takes precedence over the status filter whenever both
are supplied, since the router's own status default (ACTIVE) would otherwise
contradict an explicit resolved=true.

### Testing

Added tests/ (pytest + pytest-asyncio, sqlite+aiosqlite in-memory,
StaticPool) with 90 tests across 8 files: conftest.py (fixtures),
test_customers.py, test_products.py, test_sales_and_receipts.py,
test_payments.py, test_expenses_capital_suppliers_stock.py, test_issues.py,
test_activity_and_dashboard.py, test_http_integration.py. Run with `pytest`
from the project root.

Known limitation by design: anything depending on a PostgreSQL trigger or
function (debt-sale ledger creation, payment allocation, credit-score
recompute, v_daily_summary/v_customer_credit reads) can't be exercised
end-to-end on SQLite; tests that need that state seed it directly via the
ORM and assert on the read/decision layer this session built.

## Session 4: Smart Business Insights

New endpoint: `GET /businesses/{business_id}/insights` — a bounded,
human-readable list of recommendations and warnings for the business owner.
No schema changes. Auth follows the same owner-based pattern as the other
`/businesses/{id}/...` endpoints (not the `X-Business-Id` header pattern used
by the rest of the app) — `BusinessService.get_authorized_business` (new,
public) does the ownership check + RLS-context set, reused by
`InsightsService` so the gate is defined in exactly one place.

### Design

- `app/schemas/insights.py` — one flat shape, `InsightOut` (category,
  severity, title, message, free-form `data`), so new categories never need
  a schema change.
- `app/repositories/insights_repository.py` — only the handful of portable
  aggregate queries that didn't already exist anywhere (day-bounded sums for
  sales/expenses/payments/stock-purchases, trailing-window averages for the
  two anomaly checks). Everything else reuses existing, already-efficient
  reads: `DashboardRepository.get_margins_30d` (best-selling),
  `get_stock_attention` (slow-moving/low-stock), `get_debt_priority`
  (overdue debt), and `CustomerRepository.search(credit_level=...)`
  (high-risk/blocked, already indexed via `idx_customers_credit`).
- `app/services/insights_service.py` — a plain list of 9 generator methods
  (`_GENERATORS`), each returning 0+ `InsightOut`s; `get_insights` runs them
  all and severity-sorts the result. Adding a new insight later is: write one
  more `_xxx` method with the same signature, append it to the list — no
  other file changes.

### The 9 insight categories

Best-selling products, slow-moving products, low-stock products, overdue
debt, high-risk customers, sales trend (today vs. yesterday), expense
pressure (today's expenses vs. today's sales), cash-flow summary (always
present — see below), and unusual activity (an expense day ≥2.5× the
trailing-30-day daily average, or a single sale ≥3× the trailing-30-day
average sale).

`CASH_FLOW` is the one always-present card (current position is useful
context regardless of whether anything's wrong); every other category only
appears when there's something worth surfacing, keeping the response small.

### PostgreSQL-only generators degrade gracefully

Best-selling/slow-moving/low-stock/overdue-debt read PostgreSQL-only views.
`InsightsService._is_postgres()` checks the bind dialect and skips those four
generators entirely on any other backend (mirrors the existing pattern in
`app/workers/reconciliation.py`), rather than raising. On SQLite (tests) you
get the 5 portable insights only; on PostgreSQL (production) all 9 run.

### Tests

`tests/test_insights.py` (13 tests) covers ownership/not-found errors,
severity sorting, the always-present cash-flow baseline, high-risk/blocked
severity escalation, sales-trend up/down framing, expense-pressure
(including the "expenses with zero sales" edge case), and the unusual-
expense-spike anomaly check. Plus two HTTP-level tests in
`test_http_integration.py`. Full suite: 105 tests passing.

## Session 4: Smart Business Insights

New endpoint: `GET /businesses/{business_id}/insights` — returns a short,
severity-sorted list of human-readable recommendations/warnings for the
owner. No schema changes.

### Design

- `InsightOut` is one flat shape (`category`, `severity`, `title`, `message`,
  `data`) for every insight kind, so adding a new category later is just one
  more generator function + a literal value, no schema/router changes.
- `InsightsService._GENERATORS` is a plain list of bound methods; each
  returns 0-2 `InsightOut` items. Adding an insight = write one more method
  with that signature and append it to the list.
- Reuses existing efficient reads wherever one already existed:
  best-selling/slow-moving products and low stock come from
  `DashboardRepository` (`v_product_margin_30d`, `v_stock_attention`);
  overdue debt from `v_debt_priority`; high-risk/blocked customers from
  `CustomerRepository.search` (already indexed via `idx_customers_credit`).
  `InsightsRepository` adds only the handful of reads that didn't already
  exist — day-bounded sums for trend/cash-flow/anomaly checks — each a
  single aggregate `SELECT`, portable across SQLite and PostgreSQL.
- The three view-backed generators (best-selling, slow-moving, low-stock,
  overdue-debt) depend on PostgreSQL-only views and are skipped (return `[]`)
  rather than raising on any other backend — same pattern as
  `app/workers/reconciliation.py`.
- Authorization reuses `BusinessService.get_authorized_business` (ownership
  check + RLS session variable), the same gate every other business-scoped
  read uses — no separate auth logic for this endpoint.
- Results are sorted ALERT → WARN → INFO so the most urgent items surface
  first; the list is intentionally small and unpaginated (each generator
  caps itself at 3-5 items) since it's a "what needs my attention" glance,
  not a browsable report.

Covered by `tests/test_insights.py` (ownership/not-found guards, each
generator's trigger conditions and severity escalation, sort order) and
`tests/test_insights_http.py` (end-to-end over HTTP).

## Session 5: Reecod Customer & Supplier Scoring Engine (V1/V2)

New feature implementing the "Reecod Engine Logic Spec -- Customer & Supplier
Scoring V1/V2" document: four scores (Customer Credit Trust, Buyer Value,
Supplier Reliability, Supplier Terms), each with a strict data-readiness
gate, weight-redistribution instead of fake defaults, and the spec's exact
color/tier/copy tables.

### Migration 0005_reecod_scoring_support (additive, safe)

- `suppliers.credit_terms_days` (INTEGER, nullable)
- `suppliers.payment_flexibility` (VARCHAR(20), nullable, CHECK'd against
  FLEXIBLE/SOMETIMES_FLEXIBLE/STRICT_CASH/UNKNOWN)
- `stock_purchases.promised_delivery_date` / `actual_delivery_date` (DATE,
  nullable)
- `stock_purchase_items.quantity_ordered` (NUMERIC(15,4), nullable -- NULL
  means "not tracked separately from what was received", treated as 100%
  filled)

All nullable, no defaults that change existing row semantics, no existing
trigger/view/code reads or writes them. Zero effect on any previously
shipped feature.

### Design decision: computed on read, not stored or trigger-maintained

Unlike the existing Customer Credibility Engine (`customers.credit_score` /
`credit_level`, trigger-maintained), these four Reecod scores are computed
fresh on every `GET .../scores` request from a handful of aggregate queries.
This was a deliberate scope choice for V1:

- It automatically satisfies the spec's "edited transactions trigger
  recalculation" requirement (sec 7.4) -- there's nothing to invalidate,
  every read is current. Verified by
  `test_supplier_terms_updates_after_editing_supplier`.
- Zero new triggers/functions to write or maintain; fully portable
  (identical behaviour on SQLite and PostgreSQL).
- Tradeoff: each call runs several aggregate queries rather than reading one
  cached column. Acceptable for V1 (single-entity profile views). If bulk
  list filtering/sorting by these scores becomes a real requirement, the V2
  path is to materialize into columns + triggers, mirroring the existing
  Credibility Engine's architecture.

This module is purely additive -- it does not touch `customers.credit_score`
/ `credit_level` or anything that already consumes them (Sales debt
validation, Business Insights, the existing Customer Credibility Engine
endpoints).

### "Clean" transaction definitions (spec sec 1.1)

- Clean **credit transaction** = a `customer_debt_ledger` row with `due_date`
  set (every other required field is already schema-guaranteed non-null).
- Clean **purchase transaction** = a sale with `customer_id` set. Note: in
  this codebase, CASH sales never carry a `customer_id` even if one was
  selected at checkout (see `SaleService.create_sale`) -- only ON_DEBT sales
  do. Buyer Value is therefore computed from debt-sale history today; this
  is a known scope limit, not a bug.
- Clean **supplier order** = a `stock_purchases` row with
  `promised_delivery_date` set.

### Price Fairness benchmark (spec sec 4.2)

Uses the shop-wide recent (default 180-day) average `unit_buying_cost` for
the same `product_id` across all suppliers as the benchmark -- a simplified
combination of the spec's tier-1/tier-3 fallbacks, recomputed fresh on every
read (no stale benchmark risk). If no other purchase of that product exists
in the window, that line is excluded from the average per sec 7.1
(no fabricated benchmark).

### New endpoints

| Endpoint | Purpose |
|---|---|
| `GET /customers/{id}/scores?view_mode=trust\|value` | Credit Trust + Buyer Value, both lenses always returned |
| `GET /suppliers/{id}/scores?view_mode=reliability\|terms` | Reliability + Terms, both lenses always returned |

### Extended endpoints

- `POST/PATCH /suppliers` now accept `credit_terms_days` / `payment_flexibility`.
- `POST /stock-purchases` now accepts `promised_delivery_date` /
  `actual_delivery_date` on the header and `quantity_ordered` per line.

Covered by `tests/test_scoring.py` (19 tests: gates, redistribution, tier
boundaries, all four scores' core formulas, edit-triggers-recalculation) and
one HTTP round-trip in `tests/test_http_integration.py`.

## Session 6: Onboarding SRS v1.0 -- countries, currency-insert fix, opening-balance nudge

Scope was explicitly narrowed by product direction: implement GEO-1/GEO-2/
GEO-3 (countries) and the Section 6 dashboard nudge now; **defer
BIZ-ASSOC-1..4** (mandatory create/join wizard, invite codes, multi-user
`business_members`) for speed; GEO-4 (language) needs zero backend work,
it's client-side i18next only.

### Migration 0006_v4_countries_and_currency_fix

1. **`countries` table** (new, seeded with 12 countries) + `businesses.country_id`
   FK. Decoupled from `currency_id` on purpose (XOF/XAF are shared by several
   countries) -- `default_currency_id` is only a one-time suggestion the
   frontend applies at onboarding's currency step, never derived server-side
   afterwards. Legacy free-text `businesses.country` untouched.
2. **`businesses.opening_balance_set_at`** (nullable timestamp) -- powers the
   "set your starting cash" dashboard nudge with no separate
   `onboarding_completed` flag, per SRS Section 6's "use natural data
   signals" principle. Backfilled to `created_at` for any existing business
   with `opening_balance > 0` (already-engaged shops don't get a spurious
   nudge); left `NULL` for zero-balance businesses (correct -- they'll see
   the nudge once, same as a brand new one).
3. **Currency fix (the reported bug)** -- UPDATE only, keyed by `iso_code`,
   no id changes, so every `businesses.currency_id`/`subscription_plans.currency_id`
   FK stays valid. Replaces AED/SAR/QAR's Arabic-script/shared-Rial-glyph
   symbols with plain ASCII ("AED"/"SAR"/"QAR"), de-accents "Sao Tome and
   Principe" to plain ASCII, and re-tiers/re-sorts all 57 rows to match
   dukamate_schema_v4.sql (target markets -> rest of Africa -> global
   majors, alphabetical within each tier). This is almost certainly what
   broke your insert: an RTL/non-ASCII literal (the Arabic Rial glyph, used
   for *both* SAR and QAR) is exactly the kind of value that gets mangled by
   non-UTF-8 tooling/terminals before it reaches Postgres. Every other
   currency's existing Unicode symbol (₦, ₹, £, etc.) is left as-is --
   those are ordinary UTF-8 and not the problem.
4. **RLS `NULLIF` defensive fix** -- every existing `biz_isolation` policy is
   dropped and recreated with `NULLIF(current_setting(...),'')::UUID`
   instead of a bare cast, preventing a crash if
   `app.current_business_id` is ever unset/empty when a query reaches one
   of these tables. Same predicate otherwise -- no visibility change for a
   properly-scoped request.

### New endpoints

| Endpoint | Purpose |
|---|---|
| `GET /countries` | Onboarding wizard's country picker (name, `default_currency_id`, map center) |
| `POST /businesses/{id}/opening-balance` | Set opening cash after skipping wizard step 5; rejects a second call (`opening_balance_already_set`) so the dashboard nudge never wrongly reappears |

### Extended: `POST /businesses`

- `country_id` (optional) -- validated if given, never used to derive
  `currency_id`.
- `opening_balance` is now **optional with no forced default** (was
  `Decimal("0")`). `None` means "the owner skipped this step" and leaves
  `opening_balance_set_at` `NULL`; any explicit value (including `0`) sets
  it to now. This is the one call site that had to change shape rather than
  just gain a field -- any existing caller that always passed
  `opening_balance` explicitly is unaffected either way.
- `BusinessOut`/`BusinessDetailOut` now also return `country_id`,
  `country_ref` (nested `CountryOut`, populated when `country_id` is set),
  and `opening_balance_set_at`.

### Explicitly deferred (not built this session)

`business_members`, `business_invites`, the auto-add-owner-as-member
trigger, and `v_user_businesses` from dukamate_schema_v4.sql are **not**
created. `businesses.owner_id` remains the sole authorization source;
`BusinessService.get_authorized_business` is unchanged. This was a
deliberate scope cut for speed, not an oversight -- adding that layer later
is a self-contained migration + a swap of the ownership check to a
membership check, and nothing in this session blocks it.

Covered by `tests/test_business_country_and_opening_balance.py` (9 tests:
country validation, currency/country independence, the skip-vs-explicit-zero
distinction, the one-time-only opening-balance-set rule) plus one HTTP
round-trip in `tests/test_http_integration.py`.
