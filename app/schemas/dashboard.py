"""Pydantic v2 schemas for the Dashboard module.

All endpoints are READ-ONLY projections of the database views. No analytics are
recomputed in Python — the views own that logic.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DailySummaryOut(BaseModel):
    """Money Position + today's Sales Summary, from ``v_daily_summary`` plus
    two metrics computed directly from source tables (see
    DashboardRepository.get_today_extras): stock_purchases_today and
    profit_today are not carried by the view."""

    model_config = ConfigDict(from_attributes=True)

    business_id: uuid.UUID
    report_date: date
    sales_today: Decimal
    cash_sales_today: Decimal
    debt_sales_today: Decimal
    expenses_today: Decimal
    payments_received_today: Decimal
    stock_purchases_today: Decimal
    profit_today: Decimal
    cash_on_hand: Decimal
    total_debt_out: Decimal


class DebtPriorityRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    business_id: uuid.UUID
    full_name: str
    phone_number: str | None
    debt_balance: Decimal
    oldest_unpaid_at: datetime | None
    days_overdue: int
    open_invoices: int
    priority_score: Decimal


class DebtAgingRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ledger_id: uuid.UUID
    business_id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    sale_id: uuid.UUID
    original_amount: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal
    incurred_at: datetime
    due_date: date | None
    status: str
    risk_flag: bool
    age_days: int
    aging_bucket: str


class ActivityRow(BaseModel):
    """One entry in the unified activity feed — a single feed across every
    transaction type (sales, expenses, stock purchases, capital transactions,
    customer payments), newest first. See DashboardRepository.get_activity.
    """

    model_config = ConfigDict(from_attributes=True)

    business_id: uuid.UUID
    activity_type: Literal[
        "SALE", "EXPENSE", "STOCK_PURCHASE", "CAPITAL_TRANSACTION", "CUSTOMER_PAYMENT"
    ]
    record_id: uuid.UUID
    amount: Decimal
    occurred_at: datetime


ActivityPeriod = Literal["today", "yesterday", "custom"]


class StockAttentionRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    business_id: uuid.UUID
    category_name: str | None
    name: str
    unit: str | None
    stock_on_hand: Decimal
    low_stock_level: Decimal
    selling_price: Decimal | None
    buying_price: Decimal | None
    last_sold_at: datetime | None
    days_since_last_sale: int
    stock_status: str


class ProductMarginRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    business_id: uuid.UUID
    product_name: str
    category_name: str | None
    units_sold: Decimal
    revenue: Decimal
    cost_of_goods: Decimal
    gross_margin: Decimal
    margin_pct: Decimal | None


class ExpensePressureRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_id: uuid.UUID
    group_code: str
    group_label: str
    category_label: str
    total_spent: Decimal
    transaction_count: int


# --- list wrappers (limit + offset metadata) ---
class DebtPriorityListOut(BaseModel):
    items: list[DebtPriorityRow]


class DebtAgingListOut(BaseModel):
    items: list[DebtAgingRow]


class StockAttentionListOut(BaseModel):
    items: list[StockAttentionRow]


class ProductMarginListOut(BaseModel):
    items: list[ProductMarginRow]


class ExpensePressureListOut(BaseModel):
    items: list[ExpensePressureRow]


class ActivityListOut(BaseModel):
    items: list[ActivityRow]
    total: int
    limit: int
    offset: int


__all__ = [
    "DailySummaryOut",
    "DebtPriorityRow",
    "DebtAgingRow",
    "StockAttentionRow",
    "ProductMarginRow",
    "ExpensePressureRow",
    "ActivityRow",
    "ActivityPeriod",
    "DebtPriorityListOut",
    "DebtAgingListOut",
    "StockAttentionListOut",
    "ProductMarginListOut",
    "ExpensePressureListOut",
    "ActivityListOut",
]
