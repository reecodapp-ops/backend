# DukaMate API Reference — endpoints built in this project

Everything below was added or substantially extended in this engagement (currency system, structured location, the Customer Credibility Engine, full CRUD/filtering/sorting/pagination across every module, Receipts, the unified Activity feed, Today dashboard extras, Business Insights, the Reecod Customer & Supplier Scoring engine, and — most recently — countries + the opening-balance dashboard nudge from the Onboarding & Identity SRS v1.0).

**Deferred by explicit product direction, not built yet**: the SRS's Business Association rules (mandatory create/join wizard, invite codes, multi-user `business_members`/`business_invites`) — `owner_id` remains the sole authorization source for now. Language selection is client-side only (i18next) and needs no backend endpoint at all.

**Not included here** because they predate this work and weren't changed: `auth/*`, `corrections`, `sync`. Receipts' "correction" feature reuses the existing `POST /corrections` endpoint — see the note in the Receipts section.

All endpoints are prefixed `/api/v1` and require `Authorization: Bearer <token>`. Endpoints scoped to a specific shop also require an `X-Business-Id: <uuid>` header (noted per section). `{business_id}`-in-path endpoints (Businesses, Insights) don't need the header — ownership is checked via the path id instead.

Currency in all examples: UGX (Ugandan Shilling).

---

## 1. Lookups

| Method | Path | Purpose |
|---|---|---|
| GET | `/currencies` | All active currencies (ISO 4217) — dynamic, not hardcoded |
| GET | `/countries` | All active countries (ISO 3166-1) — onboarding wizard's country picker |
| GET | `/subscription-plans` | Available plans (FREE / STARTER / PRO) |

```
GET /api/v1/currencies
```
```json
[
  { "id": 1, "iso_code": "UGX", "name": "Ugandan Shilling", "symbol": "USh", "decimal_places": 0, "country": "Uganda", "is_active": true, "sort_order": 6 },
  { "id": 2, "iso_code": "AED", "name": "UAE Dirham", "symbol": "AED", "decimal_places": 2, "country": "United Arab Emirates", "is_active": true, "sort_order": 77 }
]
```
Tiered/alphabetized: target markets (sort_order 1–6) → rest of Africa (20–50) → global majors (60–79). AED/SAR/QAR use their plain ISO code as the display symbol (not Arabic script or a shared glyph) — a low-end-Android font-support fix.

```
GET /api/v1/countries
```
```json
[
  { "id": 6, "iso_code": "UG", "name": "Uganda", "default_currency_id": 1, "map_center_lat": "1.3733000", "map_center_lng": "32.2903000", "map_default_zoom": 6, "sort_order": 6, "is_active": true }
]
```
`default_currency_id` is a **one-time suggestion** — the client pre-fills the currency step with it when a country is picked, and the user can still override it. The backend never re-derives currency from country (or vice versa) after that; they're independent columns (several countries share one currency, e.g. XOF/XAF).

```
GET /api/v1/subscription-plans
```
```json
[
  { "id": 1, "code": "FREE", "label": "Free", "engine_phase_max": 1, "price_monthly": "0.00", "currency_id": null, "is_active": true },
  { "id": 2, "code": "STARTER", "label": "Starter", "engine_phase_max": 2, "price_monthly": "5.00", "currency_id": 39, "is_active": true }
]
```

---

## 2. Businesses & Subscriptions

| Method | Path | Purpose |
|---|---|---|
| POST | `/businesses` | Create + onboard (opening cash optional + FREE plan) |
| GET | `/businesses/{id}` | Business detail + active subscription |
| PATCH | `/businesses/{id}` | Update profile / settings |
| POST | `/businesses/{id}/opening-balance` | Set opening cash after skipping it at creation (one-time) |
| GET | `/businesses/{id}/subscriptions` | Subscription history |
| POST | `/businesses/{id}/subscription` | Change plan |
| GET | `/businesses/{id}/insights` | Smart Business Insights (see §13) |

### Create a business
```
POST /api/v1/businesses
```
```json
{
  "shop_name": "Mama Rose Shop",
  "business_type_id": 1,
  "currency_id": 1,
  "country_id": 6,
  "phone_number": "+256700111222",
  "location": "Kampala Road, Shop 14",
  "street": "Kampala Road",
  "city": "Kampala",
  "opening_balance": "50000.00"
}
```
`country_id` and `opening_balance` are both optional — the onboarding wizard's steps 2 and 5 are skippable. Omitting `opening_balance` entirely (not sending `0`) is how the wizard signals "skipped this step"; the response's `opening_balance_set_at` stays `null` until it's set, which is exactly the signal the dashboard uses to show a "set your starting cash" nudge (no separate `onboarding_completed` flag).
```json
{
  "id": "8e2f9c1a-6b3d-4e2a-9f0a-1a2b3c4d5e6f",
  "shop_name": "Mama Rose Shop",
  "currency": { "id": 1, "iso_code": "UGX", "symbol": "USh", "decimal_places": 0, "...": "..." },
  "currency_id": 1,
  "country_id": 6,
  "country_ref": { "id": 6, "iso_code": "UG", "name": "Uganda", "default_currency_id": 1, "...": "..." },
  "cash_on_hand": "50000.00",
  "total_debt_out": "0.00",
  "opening_balance": "50000.00",
  "opening_balance_set_at": "2026-07-10T09:00:00Z",
  "status": "ACTIVE",
  "active_subscription": { "id": "...", "plan_id": 1, "is_active": true, "started_at": "2026-07-02T09:00:00Z", "expires_at": null }
}
```

### Set opening cash later (skipped it at creation)
```
POST /api/v1/businesses/{business_id}/opening-balance
{ "opening_balance": "20000" }
```
Sets `opening_balance` + `cash_on_hand`, stamps `opening_balance_set_at`. Calling it again once already set returns `422 opening_balance_already_set` — the dashboard nudge is meant to disappear permanently, not toggle.

### Change subscription plan
```
POST /api/v1/businesses/{business_id}/subscription
{ "plan_id": 2 }
```
Deactivates the current plan (keeps history) and activates the new one.

```
GET /api/v1/businesses/{business_id}/subscriptions
```
```json
{ "items": [
  { "id": "...", "plan_id": 2, "is_active": true,  "started_at": "2026-07-02T09:05:00Z", "expires_at": null },
  { "id": "...", "plan_id": 1, "is_active": false, "started_at": "2026-07-02T09:00:00Z", "expires_at": "2026-07-02T09:05:00Z" }
]}
```

---

## 3. Customers — CRUD + Customer Credibility Engine (flagship feature)

Requires `X-Business-Id`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/customers` | Create |
| GET | `/customers` | Search/filter/paginate |
| GET | `/customers/{id}` | Detail |
| PATCH | `/customers/{id}` | Update |
| POST | `/customers/{id}/archive` | Soft-delete |
| GET | `/customers/{id}/credit` | Full credit profile |
| GET | `/customers/{id}/ledger` | Per-sale debt history |
| GET | `/customers/{id}/payments` | Payment + allocation history |
| GET | `/customers/{id}/scores` | Reecod Credit Trust + Buyer Value scores (see §14) |
| POST | `/customers/{id}/simulate-credit` | "Would a debt sale be approved?" |
| GET | `/customers/high-risk` | Credit board: HIGH_RISK only |
| GET | `/customers/excellent` | Credit board: EXCELLENT only |
| GET | `/customers/recommendation` | Full filterable credit board |
| GET | `/customers/credit-history` | Decision Engine credit-event timeline |

> **Two separate scoring systems exist on Customer, by design.** `/credit` (above) is the original Customer Credibility Engine — trigger-maintained in the database, drives the hard block/warn behavior on real debt sales (§3's `customer_credit_blocked` / `CREDIT_LIMIT_EXCEEDED`). `/scores` (§14) is the newer Reecod Engine spec's Credit Trust score — computed fresh on every read from a different, more granular formula (payment punctuality/debt clearance/delay severity/unpaid risk), used for the owner-facing "who can I trust" profile view. They can disagree for the same customer; neither is wrong, they answer slightly different questions. V1 keeps them independent rather than merging them — see MIGRATION_NOTES.md.

### Create + search
```
POST /api/v1/customers
{ "full_name": "Grace Nakato", "phone_number": "+256701234567" }
```

```
GET /api/v1/customers?credit_level=HIGH_RISK&overdue=true&order=debt_desc&limit=20&offset=0
```
Filters: `search`, `status`, `credit_level` (UNSCORED/BLOCKED/HIGH_RISK/AVERAGE/GOOD/EXCELLENT), `is_blocked`, `has_active_debt`, `min_debt_balance`, `max_debt_balance`, `overdue`, `recently_paid_days`. Sort via `order`: `recent`/`debt_desc`/`name_asc`/`score_asc`/`score_desc`.

### Credit profile
```
GET /api/v1/customers/{id}/credit
```
```json
{
  "customer_id": "c1a2...",
  "full_name": "Grace Nakato",
  "credit_score": 30,
  "credit_level": "HIGH_RISK",
  "is_credit_blocked": false,
  "debt_balance": "20000.00",
  "recommended_credit_limit": "15000.00",
  "available_credit": "0.00",
  "late_payment_count": 2,
  "on_time_payment_count": 1,
  "fully_paid_debt_count": 0,
  "days_overdue": 45,
  "recommendation": "Give debt with caution; consider part-payment upfront.",
  "reason": "Score reflects 0 fully paid debt(s), 1 on-time payment(s), and 2 late payment(s). High risk credit level. 2 late payment(s) on record. Oldest unpaid debt is 45 days old. Debt balance has reached the recommended limit.",
  "risk_indicators": [
    "High risk credit level",
    "2 late payment(s) on record",
    "Oldest unpaid debt is 45 days old",
    "Debt balance has reached the recommended limit"
  ]
}
```

### Simulate a debt sale before committing
```
POST /api/v1/customers/{id}/simulate-credit
{ "requested_amount": "10000" }
```
```json
{
  "would_be_approved": false,
  "would_exceed_limit": true,
  "current_score": 30,
  "current_level": "HIGH_RISK",
  "projected_debt_balance": "30000.00",
  "remaining_available_credit": "0.00",
  "recommendation": "This would bring their debt to 30000.00, above the recommended limit of 15000.00. Consider a smaller amount or partial upfront payment."
}
```

### Ledger, payment history, credit board
```
GET /api/v1/customers/{id}/ledger?status=PARTIAL&limit=10
```
```json
{ "items": [
  { "id": "...", "sale_id": "...", "original_amount": "20000.00", "paid_amount": "5000.00",
    "remaining_amount": "15000.00", "status": "PARTIAL", "risk_flag": true, "due_date": null }
], "total": 1, "limit": 10, "offset": 0 }
```

```
GET /api/v1/customers/{id}/payments?limit=10
```
```json
{ "items": [
  { "id": "...", "amount": "5000.00", "occurred_at": "2026-06-28T10:00:00Z",
    "allocations": [ { "sale_id": "...", "allocated_amount": "5000.00", "ledger_status": "PARTIAL", "ledger_remaining": "15000.00" } ] }
]}
```

```
GET /api/v1/customers/high-risk?limit=10
GET /api/v1/customers/recommendation?credit_level=EXCELLENT&order=score_desc
GET /api/v1/customers/credit-history?limit=20          # business-wide
GET /api/v1/customers/credit-history?customer_id=c1a2...  # one customer
```
```json
{ "items": [
  { "issue_id": "...", "customer_name": "Grace Nakato", "event_code": "CUSTOMER_HIGH_RISK",
    "headline": "Customer credit risk is high", "severity": "WARN", "created_at": "2026-06-15T08:00:00Z" }
]}
```

---

## 4. Products & Categories

Requires `X-Business-Id`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/product-categories` | Create category |
| GET | `/product-categories` | List (system defaults + own) |
| PATCH | `/product-categories/{id}` | Update |
| POST | `/products` | Create product |
| GET | `/products` | Search/filter/paginate |
| GET | `/products/{id}` | Detail |
| PATCH | `/products/{id}` | Update |
| POST | `/products/{id}/archive` | Soft-delete |

```
POST /api/v1/products
{ "name": "Sugar 1kg", "category_id": 1, "selling_price": "3500", "buying_price": "3000", "stock_qty": "40", "low_stock_level": "10" }
```
```json
{ "product": { "id": "...", "name": "Sugar 1kg", "stock_on_hand": "40.0000", "status": "ACTIVE" }, "warnings": [] }
```
A near-duplicate name (e.g. `"Sugar 1Kg"`) returns a `DUPLICATE_ITEM` warning instead of blocking.

```
GET /api/v1/products?stock_status=LOW_STOCK&min_price=1000&max_price=10000&order=price_asc&supplier_id=<uuid>
```
Filters: `search`, `category_id`, `status`, `stock_status` (OUT_OF_STOCK/LOW_STOCK/OK), `min_price`, `max_price`, `supplier_id` (products bought from that supplier). Sort: `name_asc`/`stock_asc`/`stock_desc`/`price_asc`/`price_desc`/`recent`.

---

## 5. Suppliers

Requires `X-Business-Id`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/suppliers` | Create |
| GET | `/suppliers` | Search/paginate (`order=name_asc\|recent`) |
| GET | `/suppliers/{id}` | Detail |
| PATCH | `/suppliers/{id}` | Update |
| POST | `/suppliers/{id}/archive` | Soft-delete |
| GET | `/suppliers/{id}/scores` | Reecod Reliability + Terms scores (see §14) |

```json
POST /api/v1/suppliers
{
  "name": "Kato Supplies",
  "phone_number": "+256772000111",
  "credit_terms_days": 14,
  "payment_flexibility": "FLEXIBLE"
}
```
`credit_terms_days` and `payment_flexibility` (`FLEXIBLE`/`SOMETIMES_FLEXIBLE`/`STRICT_CASH`/`UNKNOWN`) are optional Reecod Supplier Terms Score inputs — omit them and that score just drops the corresponding factor.

---

## 6. Sales & Receipts


Requires `X-Business-Id`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/sales` | Record a cash or debt sale |
| GET | `/sales` | Filter/sort/paginate |
| GET | `/sales/today` | Today's sales |
| GET | `/sales/{id}` | Detail |
| GET | `/sales/{id}/receipt` | Receipt preview (read-only) |
| POST | `/sales/{id}/receipt/regenerate` | Re-issue receipt number |
| POST | `/sales/{id}/share-receipt` | Record share + optional WhatsApp link |

### Cash sale
```json
POST /api/v1/sales
{ "payment_type": "CASH", "items": [
  { "product_name": "Sugar 1kg", "quantity": "2", "unit_price": "3500" }
]}
```
```json
{ "sale": { "id": "...", "total_amount": "7000.00", "outstanding_amount": "0.00", "receipt_number": "RCP-20260702-0001" }, "warnings": [] }
```

### Debt sale — validation & soft warnings
```json
POST /api/v1/sales
{ "payment_type": "ON_DEBT", "customer_id": "c1a2...", "items": [
  { "product_name": "Cooking Oil 2L", "quantity": "1", "unit_price": "18000" }
]}
```
- Customer archived → `422 customer_not_active`
- Customer blocked → `422 customer_credit_blocked`
- New customer (UNSCORED) → sale succeeds, warning `NEW_CUSTOMER_NO_HISTORY`
- Would exceed `recommended_credit_limit` → sale still succeeds, warning `CREDIT_LIMIT_EXCEEDED`
- Selling more than `stock_on_hand` → warning `OVERSELL`

```
GET /api/v1/sales?payment_type=ON_DEBT&customer_id=c1a2...&order=amount_desc
```
Filters: `start_date`, `end_date`, `customer_id`, `payment_type` (CASH/ON_DEBT), `receipt_number` (partial match). Sort: `recent`/`oldest`/`amount_desc`/`amount_asc`.

### Receipt preview / share / regenerate
```
GET /api/v1/sales/{id}/receipt
```
```json
{
  "receipt_number": "RCP-20260702-0001",
  "business_name": "Mama Rose Shop",
  "currency_symbol": "USh",
  "items": [ { "product_name": "Sugar 1kg", "quantity": "2", "unit_price": "3500", "line_total": "7000.00" } ],
  "total_amount": "7000.00",
  "footer": "Mama Rose Shop\nTel: +256700111222\nKampala Road, Shop 14\nThank you for your business!",
  "whatsapp_share_url": null
}
```
```json
POST /api/v1/sales/{id}/share-receipt
{ "shared_to": "CUSTOMER", "destination_number": "+256701234567" }
```
Returns the same receipt shape with `whatsapp_share_url`: `https://wa.me/256701234567?text=...`. The number is **not persisted** (no column for it) — it's only used to build the deep link.

```
POST /api/v1/sales/{id}/receipt/regenerate
```
Re-issues `receipt_number` (e.g. `RCP-20260702-0002`); nothing financial changes.

**Receipt correction** — not a new endpoint. Use the existing `POST /corrections` with `correction_type_code: "SALE_EDIT"`.

---

## 7. Customer Payments

Requires `X-Business-Id`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/customer-payments` | Record a payment (oldest-debt-first allocation) |
| GET | `/customer-payments` | History (`?customer_id=`) |

```json
POST /api/v1/customer-payments
{ "customer_id": "c1a2...", "amount": "10000" }
```
```json
{
  "payment": { "id": "...", "amount": "10000.00" },
  "allocations": [ { "sale_id": "...", "allocated_amount": "10000.00", "ledger_status": "PARTIAL", "ledger_remaining": "10000.00" } ],
  "customer_debt_balance_after": "10000.00",
  "unallocated_amount": "0.00",
  "warnings": []
}
```
Paying more than the outstanding balance returns warning `OVERPAYMENT` and a non-zero `unallocated_amount`.

---

## 8. Expenses

Requires `X-Business-Id`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/expense-categories` | List visible categories |
| POST | `/expenses` | Record an expense |
| GET | `/expenses` | Filter/sort/paginate |
| GET | `/expenses/{id}` | Detail |

```json
POST /api/v1/expenses
{ "category_id": 1, "amount": "15000", "note": "Shop rent", "occurred_at": "2026-07-02T08:00:00Z" }
```
An amount well above the category's recent average returns warning `UNUSUAL_AMOUNT`.

```
GET /api/v1/expenses?group_id=1&search=rent&min_amount=5000&order=amount_desc&from=2026-06-01T00:00:00Z
```
`group_id`: 1 = Business expense, 2 = Personal use. Filters: `category_id`, `group_id`, `search` (note/supplier_name), `from`/`to`, `min_amount`/`max_amount`. Sort: `recent`/`oldest`/`amount_desc`/`amount_asc`.

---

## 9. Stock Purchases

Requires `X-Business-Id`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/stock-purchases` | Record a purchase (stock increments via trigger) |
| GET | `/stock-purchases` | Filter/sort/paginate |
| GET | `/stock-purchases/{id}` | Detail |

```json
POST /api/v1/stock-purchases
{
  "supplier_id": "<uuid>",
  "promised_delivery_date": "2026-07-10",
  "actual_delivery_date": "2026-07-12",
  "items": [
    { "product_name": "Maize Flour 50kg", "quantity": "8", "quantity_ordered": "10", "total_item_cost": "400000" }
  ]
}
```
`promised_delivery_date`/`actual_delivery_date` and `quantity_ordered` are optional — feeding them enables the Reecod Supplier Reliability Score (§14). Omit them and that supplier's reliability score simply won't have delivery data to work with yet.
```
GET /api/v1/stock-purchases?supplier_id=<uuid>&search=urgent&order=cost_desc
```

---

## 10. Capital

Requires `X-Business-Id`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/capital-source-types` | Seeded source list (for MONEY_IN) |
| POST | `/capital-transactions` | Record MONEY_IN / MONEY_OUT / PERSONAL_USE |
| GET | `/capital-transactions` | Filter/paginate |
| GET | `/capital-transactions/{id}` | Detail |

```json
POST /api/v1/capital-transactions
{ "type_code": "MONEY_IN", "source_type_id": 1, "amount": "200000", "note": "Personal top-up" }
```

```
GET /api/v1/capital-transactions?type=OPENING_BALANCE
```
`OPENING_BALANCE` can never be *created* here (would double-count `businesses.opening_balance`) but **is** a valid list/detail filter — a read-only synthetic entry is generated from the business's own opening balance so it shows up in history like any other transaction.

---

## 11. Issues (Decision Engine)

Requires `X-Business-Id`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/issues` | Filter/paginate |
| PATCH | `/issues/{id}` | `{ "status": "RESOLVED" \| "IGNORED" }` |
| POST | `/issues/recompute` | Run the Phase-1 rule engine now |

```
GET /api/v1/issues?severity=ALERT&customer_id=c1a2...&resolved=false
```
Filters: `status`, `min_confidence`, `severity`, `customer_id`, `product_id`, `resolved` (takes precedence over `status` if both given). Issue types include both the Phase-1 engine (`STOCK_FINISHING`, `DEBT_OVERDUE`, `LOW_MARGIN`, `EXPENSE_PRESSURE`) and the Customer Credibility Engine (`CUSTOMER_HIGH_RISK`, `CUSTOMER_EXCELLENT`, `CREDIT_LIMIT_REACHED`, `PAYMENT_OVERDUE`, `REPEATED_DEFAULT`).

---

## 12. Dashboard

Requires `X-Business-Id`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/dashboard/today` | Today's summary (sales/expenses/stock/profit/cash) |
| GET | `/dashboard/activity` | Unified activity feed |
| GET | `/dashboard/debt-priority` | Who to chase first |
| GET | `/dashboard/debt-aging` | Per-sale debt aging buckets |
| GET | `/dashboard/stock-attention` | Stock status buckets |
| GET | `/dashboard/margins` | 30-day product margins |
| GET | `/dashboard/expense-pressure` | 30-day expense breakdown |

```
GET /api/v1/dashboard/today
```
```json
{
  "sales_today": "45000.00", "cash_sales_today": "30000.00", "debt_sales_today": "15000.00",
  "expenses_today": "8000.00", "payments_received_today": "10000.00",
  "stock_purchases_today": "20000.00", "profit_today": "18000.00",
  "cash_on_hand": "540000.00", "total_debt_out": "185000.00"
}
```

```
GET /api/v1/dashboard/activity?period=yesterday
GET /api/v1/dashboard/activity?period=custom&from=2026-06-01T00:00:00Z&to=2026-06-30T23:59:59Z&limit=50
```
```json
{ "items": [
  { "activity_type": "SALE", "record_id": "...", "amount": "7000.00", "occurred_at": "2026-07-02T09:10:00Z" },
  { "activity_type": "CUSTOMER_PAYMENT", "record_id": "...", "amount": "10000.00", "occurred_at": "2026-07-02T08:00:00Z" }
], "total": 12, "limit": 50, "offset": 0 }
```
`period`: `today` / `yesterday` / `custom` (with `from`/`to`). Merges sales, payments, stock purchases, expenses, and capital transactions in one feed, newest first.

---

## 13. Business Insights (flagship analytics)

| Method | Path | Purpose |
|---|---|---|
| GET | `/businesses/{business_id}/insights` | Human-readable recommendations & warnings |

```
GET /api/v1/businesses/{business_id}/insights
```
```json
{
  "business_id": "8e2f9c1a-...",
  "generated_at": "2026-07-02T09:15:03Z",
  "items": [
    { "category": "HIGH_RISK_CUSTOMERS", "severity": "ALERT",
      "title": "Customers with risky credit standing",
      "message": "2 customer(s) are HIGH_RISK and 1 are BLOCKED, including Peter Otieno, Grace Nakato, John Mukasa. Be cautious extending more debt.",
      "data": { "high_risk_count": 2, "blocked_count": 1 } },
    { "category": "OVERDUE_DEBT", "severity": "ALERT",
      "title": "Customers owe you overdue debt",
      "message": "4 customer(s) have overdue debt totalling 185000.00. Highest priority: Grace Nakato (95000.00), Peter Otieno (60000.00)." },
    { "category": "SALES_TREND", "severity": "WARN",
      "title": "Today vs. yesterday",
      "message": "Today's sales (12000.00) are down 65% compared to yesterday (34000.00)." },
    { "category": "LOW_STOCK_PRODUCTS", "severity": "WARN",
      "title": "Stock running low",
      "message": "0 item(s) are out of stock and 3 are running low, including Sugar 1kg, Cooking Oil 2L, Rice 5kg." },
    { "category": "SLOW_MOVING_PRODUCTS", "severity": "INFO",
      "title": "Some items haven't sold in a while",
      "message": "5 product(s) haven't sold in over 14 days, including Umbrellas, Birthday Candles, Shoe Polish." },
    { "category": "BEST_SELLING_PRODUCTS", "severity": "INFO",
      "title": "Your best sellers this month",
      "message": "Top performers by revenue in the last 30 days: Bread (450000.00), Milk 1L (320000.00), Soap Bar (210000.00)." },
    { "category": "CASH_FLOW", "severity": "INFO",
      "title": "Cash position",
      "message": "You currently have 540000.00 in cash and 185000.00 owed to you by customers. Net cash movement today: +8000.00." }
  ]
}
```
Categories: `BEST_SELLING_PRODUCTS`, `SLOW_MOVING_PRODUCTS`, `LOW_STOCK_PRODUCTS`, `OVERDUE_DEBT`, `HIGH_RISK_CUSTOMERS`, `SALES_TREND`, `EXPENSE_PRESSURE`, `CASH_FLOW`, `UNUSUAL_ACTIVITY`. Sorted `ALERT` → `WARN` → `INFO`; `CASH_FLOW` is always present, everything else only appears when there's something worth flagging. `BEST_SELLING_PRODUCTS`/`SLOW_MOVING_PRODUCTS`/`LOW_STOCK_PRODUCTS`/`OVERDUE_DEBT` require PostgreSQL (they read database views).

---

## 14. Reecod Customer & Supplier Scoring (V1/V2)

Implements the "Reecod Engine Logic Spec — Customer & Supplier Scoring V1/V2" document. Four scores, each gated by a minimum clean-record threshold, each returning `insufficient_data` rather than a fabricated number when the data isn't there yet.

| Method | Path | Purpose |
|---|---|---|
| GET | `/customers/{id}/scores?view_mode=trust\|value` | Credit Trust + Buyer Value |
| GET | `/suppliers/{id}/scores?view_mode=reliability\|terms` | Reliability + Terms |

Both endpoints always return **every** lens for that entity — `active_view` just tells the frontend which one to show as the single big primary score (spec's "Single Lens UI Rule"). `status` is `"calculated"` or `"insufficient_data"`; `percentage`/`color` are `null` whenever the latter.

### Customer scores — new customer (below the 3-clean-transaction gate)
```
GET /api/v1/customers/{id}/scores
```
```json
{
  "customer_id": "c1a2...",
  "name": "Amina",
  "active_view": "trust",
  "scores": {
    "trust": {
      "percentage": null, "color": null, "label": "New", "status": "insufficient_data",
      "meaning": "Not enough clean credit records yet to calculate a trust score.",
      "action": "Record due dates on credit sales to start building this score.",
      "reason": "Needs 3 credit records to calculate."
    },
    "value": {
      "percentage": null, "color": null, "label": "New", "status": "insufficient_data",
      "meaning": "Not enough purchase history yet to calculate buyer value.",
      "action": "No action yet — this customer is still new.",
      "reason": "Needs 3 purchases to calculate."
    }
  }
}
```

### Customer scores — established customer
```
GET /api/v1/customers/{id}/scores?view_mode=trust
```
```json
{
  "customer_id": "c1a2...",
  "name": "Amina",
  "active_view": "trust",
  "scores": {
    "trust": {
      "percentage": 82, "color": "green", "label": "Trusted", "status": "calculated",
      "meaning": "This customer usually pays and has low unpaid risk.",
      "action": "Safe to offer normal credit.",
      "factors": { "payment_punctuality": 100.0, "debt_clearance": 90.0, "delay_severity": 100.0, "current_unpaid_risk": 60.0 }
    },
    "value": {
      "percentage": 94, "color": "green", "label": "Top Buyer", "status": "calculated",
      "meaning": "This customer buys more than the average customer.",
      "action": "Consider loyalty treatment or priority service.",
      "factors": { "purchase_frequency": 100.0, "purchase_volume": 88.0 }
    }
  }
}
```
`factors` is the raw weighted breakdown — always safe to ignore, useful if you want to show/debug the math. Missing factors (e.g. no closed credit transactions yet to measure punctuality) are simply absent from `factors`, never defaulted to a placeholder — their weight was redistributed across whatever did compute.

### Supplier scores
```
GET /api/v1/suppliers/{id}/scores?view_mode=reliability
```
```json
{
  "supplier_id": "s45...",
  "name": "Kato Supplies",
  "active_view": "reliability",
  "scores": {
    "reliability": {
      "percentage": 68, "color": "yellow", "label": "Fair", "status": "calculated",
      "meaning": "This supplier usually delivers, but not always consistently.",
      "action": "Usable, but compare before depending fully.",
      "factors": { "delivery_punctuality": 66.7, "order_fill_rate": 95.0, "delivery_delay_severity": 50.0 }
    },
    "terms": {
      "percentage": 79, "color": "green", "label": "Strong Terms", "status": "calculated",
      "meaning": "This supplier offers good pricing or flexible payment terms.",
      "action": "Consider prioritizing for planned orders.",
      "factors": { "price_fairness": 95.0, "credit_terms": 100.0, "payment_flexibility": 60.0 }
    }
  }
}
```

### The four gates and what feeds them

| Score | Minimum data | Fed by |
|---|---|---|
| Credit Trust | 3 debt-ledger entries **with a due date recorded** | Debt sales' `due_date`, payments' timing |
| Buyer Value | 3 purchases linked to the customer | Debt sales (see note below) |
| Reliability | 3 stock purchases **with `promised_delivery_date` recorded** | `POST /stock-purchases`'s delivery fields |
| Terms | 3 stock purchases with a recorded buying cost | Item pricing, supplier's `credit_terms_days`/`payment_flexibility` |

**Note on Buyer Value**: in this backend, a CASH sale is never attributed to a customer even if one was picked at checkout — only `ON_DEBT` sales carry `customer_id`. So today, Buyer Value is effectively computed from a customer's debt-purchase history, not their cash purchases. Worth knowing if a cash-heavy customer's Buyer Value looks lower than expected.

**These are separate from `/customers/{id}/credit`** (§3) — see the note there for why both exist and when to use which.

---



```bash
BASE="http://localhost:8000/api/v1"
TOKEN="<access token from /auth/login>"

# Onboard a shop
BIZ=$(curl -s -X POST $BASE/businesses -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"shop_name":"Mama Rose Shop","business_type_id":1,"currency_id":1}' | jq -r .id)
AUTH_HEADERS=(-H "Authorization: Bearer $TOKEN" -H "X-Business-Id: $BIZ" -H "Content-Type: application/json")

# Add a customer, a product, and a debt sale
CUST=$(curl -s -X POST $BASE/customers "${AUTH_HEADERS[@]}" -d '{"full_name":"Grace Nakato"}' | jq -r .id)
curl -s -X POST $BASE/products "${AUTH_HEADERS[@]}" -d '{"name":"Sugar 1kg","selling_price":"3500","stock_qty":"40"}' > /dev/null
SALE=$(curl -s -X POST $BASE/sales "${AUTH_HEADERS[@]}" \
  -d "{\"payment_type\":\"ON_DEBT\",\"customer_id\":\"$CUST\",\"items\":[{\"product_name\":\"Sugar 1kg\",\"quantity\":\"5\",\"unit_price\":\"3500\"}]}" | jq -r .sale.id)

# Check credit, receipt, dashboard, insights, and Reecod scores
curl -s $BASE/customers/$CUST/credit "${AUTH_HEADERS[@]}" | jq
curl -s $BASE/customers/$CUST/scores "${AUTH_HEADERS[@]}" | jq
curl -s $BASE/sales/$SALE/receipt "${AUTH_HEADERS[@]}" | jq
curl -s $BASE/dashboard/today "${AUTH_HEADERS[@]}" | jq
curl -s $BASE/businesses/$BIZ/insights -H "Authorization: Bearer $TOKEN" | jq
```
