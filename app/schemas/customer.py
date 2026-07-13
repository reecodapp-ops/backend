"""Pydantic v2 schemas for the Customer module."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.phone_fields import PhonePairFields

PHONE_RE = re.compile(r"^\+\d{8,15}$")
CustomerStatus = Literal["ACTIVE", "ARCHIVED"]
CreditLevel = Literal["UNSCORED", "BLOCKED", "HIGH_RISK", "AVERAGE", "GOOD", "EXCELLENT"]


def _normalize_optional_phone(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().replace(" ", "")
    if v == "":
        return None
    if not PHONE_RE.match(v):
        raise ValueError("phone_number must be international format, e.g. +256700000000")
    return v


class CustomerCreateRequest(PhonePairFields):
    full_name: str = Field(..., min_length=1, max_length=200)
    phone_number: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None)

    @field_validator("full_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("full_name cannot be empty")
        return v

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _normalize_optional_phone(v)


class CustomerUpdateRequest(PhonePairFields):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone_number: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None)
    status: CustomerStatus | None = Field(default=None)

    @field_validator("full_name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("full_name cannot be empty")
        return v

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _normalize_optional_phone(v)


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    full_name: str
    phone_number: str | None
    notes: str | None
    debt_balance: Decimal
    oldest_unpaid_at: datetime | None
    total_paid: Decimal
    total_debt_issued: Decimal
    last_transaction_at: datetime | None

    # ----- Customer Credibility Engine [NEW v3] — read-only, system-computed --
    credit_score: int | None
    credit_level: CreditLevel
    is_credit_blocked: bool
    recommended_credit_limit: Decimal
    average_payment_days: Decimal | None
    late_payment_count: int
    on_time_payment_count: int
    fully_paid_debt_count: int
    last_credit_review: datetime | None

    status: str
    created_at: datetime
    updated_at: datetime


class CustomerListOut(BaseModel):
    items: list[CustomerOut]
    total: int
    limit: int
    offset: int


class CustomerCreditOut(BaseModel):
    """Projection of ``v_customer_credit`` (NEW v3) — the read model behind the
    credit UI and the "can I give this customer more debt?" prompt at the point
    of sale. Read-only: nothing here is ever written by the application.

    ``reason`` and ``risk_indicators`` are composed in the service layer from
    the already-computed, trigger-maintained counters on ``customers`` — they
    explain the stored score, they never recompute it. The scoring formula
    itself lives exclusively in the database function
    ``recompute_customer_credit_score()``.
    """

    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    business_id: uuid.UUID
    full_name: str
    phone_number: str | None
    credit_score: int | None
    credit_level: CreditLevel
    is_credit_blocked: bool
    debt_balance: Decimal
    recommended_credit_limit: Decimal
    available_credit: Decimal
    average_payment_days: Decimal | None
    on_time_payment_count: int
    late_payment_count: int
    fully_paid_debt_count: int
    days_overdue: int
    last_credit_review: datetime | None
    recommendation: str
    reason: str
    risk_indicators: list[str]


class CustomerCreditListOut(BaseModel):
    """Business-wide credit board — backs ``/customers/high-risk``,
    ``/customers/excellent`` and ``/customers/recommendation``."""

    items: list[CustomerCreditOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- #
# Debt ledger (GET /customers/{id}/ledger)
# --------------------------------------------------------------------------- #
LedgerStatus = Literal["UNPAID", "PARTIAL", "PAID", "WRITTEN_OFF"]


class LedgerPaymentOut(BaseModel):
    """One payment that was allocated against a specific debt-ledger row
    (via ``payment_allocations``) -- the "which payment(s) closed this debt"
    detail. For a still-open (UNPAID) row this list is simply empty."""

    payment_id: uuid.UUID
    amount: Decimal
    occurred_at: datetime
    note: str | None = None


class CustomerLedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sale_id: uuid.UUID
    receipt_number: str | None = Field(
        default=None,
        description=(
            "The sale's human-readable receipt number (sales.receipt_number, "
            "joined via sale_id). Never null for a real sale -- it's always "
            "set on creation."
        ),
    )
    original_amount: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal
    incurred_at: datetime
    due_date: date | None
    fully_paid_at: datetime | None
    status: LedgerStatus
    risk_flag: bool
    payments: list[LedgerPaymentOut] = []


class CustomerLedgerListOut(BaseModel):
    items: list[CustomerLedgerEntryOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- #
# Payment history + allocations (GET /customers/{id}/payments)
# --------------------------------------------------------------------------- #
class CustomerPaymentAllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    debt_ledger_id: uuid.UUID
    sale_id: uuid.UUID
    allocated_amount: Decimal
    ledger_status: LedgerStatus
    ledger_remaining: Decimal


class CustomerPaymentHistoryEntryOut(BaseModel):
    """One payment plus exactly which debts it was allocated to — the
    "payment allocation history" the flagship feature asks for."""

    id: uuid.UUID
    amount: Decimal
    occurred_at: datetime
    note: str | None
    is_correction: bool
    allocations: list[CustomerPaymentAllocationOut]


class CustomerPaymentHistoryListOut(BaseModel):
    items: list[CustomerPaymentHistoryEntryOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- #
# Credit simulation (POST /customers/{id}/simulate-credit)
# --------------------------------------------------------------------------- #
class CreditSimulationRequest(BaseModel):
    requested_amount: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)


class CreditSimulationOut(BaseModel):
    """"Should I give this customer {amount} on credit?" — a pure projection
    against already-computed credit fields. Never mutates anything and never
    recomputes the score; it only evaluates the requested amount against the
    customer's current, trigger-maintained standing.
    """

    customer_id: uuid.UUID
    requested_amount: Decimal
    would_be_approved: bool
    would_exceed_limit: bool
    is_credit_blocked: bool
    current_score: int | None
    current_level: CreditLevel
    current_debt_balance: Decimal
    projected_debt_balance: Decimal
    recommended_credit_limit: Decimal
    remaining_available_credit: Decimal
    recommendation: str


# --------------------------------------------------------------------------- #
# Credit event timeline (GET /customers/credit-history)
# --------------------------------------------------------------------------- #
CreditEventCode = Literal[
    "CUSTOMER_HIGH_RISK",
    "CUSTOMER_EXCELLENT",
    "CREDIT_LIMIT_REACHED",
    "PAYMENT_OVERDUE",
    "REPEATED_DEFAULT",
]


class CreditEventOut(BaseModel):
    """One Decision Engine event about a customer's credit standing.

    This is DukaMate's "credit score history": the database doesn't persist a
    row per score recalculation (only the current score), but every level
    transition and risk event is durably recorded as an ``issues`` row by
    ``recompute_customer_credit_score()`` — CUSTOMER_HIGH_RISK,
    CUSTOMER_EXCELLENT, CREDIT_LIMIT_REACHED, REPEATED_DEFAULT — and by
    ``raise_overdue_debt_issues()`` — PAYMENT_OVERDUE. This projection reads
    that timeline back out, newest first.
    """

    model_config = ConfigDict(from_attributes=True)

    issue_id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    event_code: CreditEventCode
    headline: str
    body_text: str | None
    severity: Literal["INFO", "WARN", "ALERT"]
    data_snapshot: dict | None
    status: Literal["ACTIVE", "RESOLVED", "IGNORED", "SUPERSEDED"]
    created_at: datetime


class CreditEventListOut(BaseModel):
    items: list[CreditEventOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- #
# Filters shared by GET /customers, /customers/high-risk, /customers/excellent,
# /customers/recommendation
# --------------------------------------------------------------------------- #
DebtStatusFilter = Literal["ANY", "HAS_DEBT", "NO_DEBT"]


__all__ = [
    "CustomerCreateRequest",
    "CustomerUpdateRequest",
    "CustomerOut",
    "CustomerListOut",
    "CustomerCreditOut",
    "CustomerCreditListOut",
    "CustomerStatus",
    "CreditLevel",
    "LedgerStatus",
    "CustomerLedgerEntryOut",
    "LedgerPaymentOut",
    "CustomerLedgerListOut",
    "CustomerPaymentAllocationOut",
    "CustomerPaymentHistoryEntryOut",
    "CustomerPaymentHistoryListOut",
    "CreditSimulationRequest",
    "CreditSimulationOut",
    "CreditEventCode",
    "CreditEventOut",
    "CreditEventListOut",
    "DebtStatusFilter",
]
