"""Pydantic v2 schemas for the Business Setup module.

Covers create (onboarding), partial update (settings), and read responses.
Money values use Decimal; currency is a dynamic reference to the ``currencies``
lookup table (loaded from the database -- never hardcoded), status uses
constrained types.

[NEW v4] ``country_id`` is a dynamic reference to the ``countries`` lookup
table, decoupled from ``currency_id`` on purpose (several countries share one
currency) -- see app/models/lookups.py::Country. ``opening_balance`` is now
optional (default ``None``, not ``0``) so the backend can tell "the owner
skipped this wizard step" apart from "the owner explicitly entered zero" --
see BusinessService and the ``opening_balance_set_at`` dashboard-nudge field.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.phone_fields import PhonePairFields

PHONE_RE = re.compile(r"^\+\d{8,15}$")

BusinessStatus = Literal["ACTIVE", "SUSPENDED", "ARCHIVED"]


def _normalize_optional_phone(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().replace(" ", "")
    if v == "":
        return None
    if not PHONE_RE.match(v):
        raise ValueError("phone_number must be international format, e.g. +256700000000")
    return v


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class BusinessCreateRequest(PhonePairFields):
    shop_name: str = Field(..., min_length=1, max_length=200)
    business_type_id: int = Field(..., ge=1)
    phone_number: str | None = Field(default=None, max_length=20)

    # ----- Location -----
    # Legacy free-text field kept for backward compatibility; a client may
    # send this alone, the structured fields alone, or both.
    location: str | None = Field(default=None, max_length=300)
    street: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    country: str | None = Field(
        default=None, max_length=100,
        description="Legacy free-text country name. Prefer country_id.",
    )
    country_id: int | None = Field(
        default=None, ge=1,
        description=(
            "References the `countries` table [NEW v4]. Drives map center and "
            "the onboarding wizard's currency suggestion client-side -- never "
            "used to derive currency_id server-side (they're independent)."
        ),
    )
    formatted_address: str | None = Field(default=None, max_length=400)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=10, decimal_places=7)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=10, decimal_places=7)
    place_id: str | None = Field(default=None, max_length=255)

    # ----- Currency [CHANGED v3] -----
    # References a row in the `currencies` table. Any currency present in the
    # database is valid -- new markets are supported by inserting a row, with
    # zero code changes.
    currency_id: int = Field(..., ge=1)

    # [CHANGED v4] Optional, no forced default -- wizard step 5 is skippable
    # (Onboarding SRS Section 3). `None` means "the owner skipped this step",
    # distinct from an explicit 0.
    opening_balance: Decimal | None = Field(
        default=None, ge=0, max_digits=15, decimal_places=2
    )
    opening_date: date | None = Field(default=None)

    @field_validator("shop_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("shop_name cannot be empty")
        return v

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _normalize_optional_phone(v)


class BusinessUpdateRequest(PhonePairFields):
    """Profile/settings update. Balances and aggregates are NOT editable here
    -- see ``SetOpeningBalanceRequest`` / ``POST .../opening-balance`` for the
    one balance field that can be set after creation."""

    shop_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone_number: str | None = Field(default=None, max_length=20)

    location: str | None = Field(default=None, max_length=300)
    street: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    country_id: int | None = Field(default=None, ge=1)
    formatted_address: str | None = Field(default=None, max_length=400)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=10, decimal_places=7)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=10, decimal_places=7)
    place_id: str | None = Field(default=None, max_length=255)

    currency_id: int | None = Field(default=None, ge=1)
    business_type_id: int | None = Field(default=None, ge=1)
    logo_url: str | None = Field(default=None, max_length=500)
    status: BusinessStatus | None = Field(default=None)

    @field_validator("shop_name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("shop_name cannot be empty")
        return v

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return _normalize_optional_phone(v)


class SetOpeningBalanceRequest(BaseModel):
    """POST /businesses/{id}/opening-balance -- lets an owner who skipped
    wizard step 5 set it later from the dashboard nudge (SRS Section 6)."""

    opening_balance: Decimal = Field(..., ge=0, max_digits=15, decimal_places=2)


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: int
    is_active: bool
    started_at: datetime
    expires_at: datetime | None


class CurrencyOut(BaseModel):
    """A row from the ``currencies`` lookup table (NEW v3).

    Returned nested on ``BusinessOut``/``BusinessDetailOut`` and via
    ``GET /currencies`` for onboarding/settings currency pickers.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    iso_code: str
    name: str
    symbol: str
    decimal_places: int
    country: str | None
    is_active: bool
    sort_order: int


class CountryOut(BaseModel):
    """A row from the ``countries`` lookup table (NEW v4; ``calling_code`` +
    full 242-country seed added by the country-phone-codes migration).

    Returned nested on ``BusinessOut``/``BusinessDetailOut`` and via
    ``GET /countries`` for the onboarding wizard's country picker AND the
    phone-number country-code picker used everywhere a phone number is
    entered (business, customer, supplier). ``default_currency_id`` is a
    one-time suggestion the client applies when the user picks a country --
    never re-derived server-side afterwards, and is ``null`` for every
    country outside the pinned target-market tier (deliberate -- see the
    country-phone-codes migration's own note).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    iso_code: str
    name: str
    calling_code: str
    default_currency_id: int | None
    map_center_lat: Decimal | None
    map_center_lng: Decimal | None
    map_default_zoom: int
    sort_order: int
    is_active: bool


class BusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    business_type_id: int
    shop_name: str
    phone_number: str | None

    location: str | None
    street: str | None
    city: str | None
    district: str | None
    country: str | None
    country_id: int | None
    country_ref: CountryOut | None = Field(default=None, description="Populated when country_id is set.")
    formatted_address: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    place_id: str | None

    currency_id: int
    currency: CurrencyOut

    opening_balance: Decimal
    opening_balance_set_at: datetime | None = Field(
        default=None,
        description=(
            "NULL until an owner explicitly sets opening cash. Frontend uses "
            "this (per SRS Section 6) to decide whether to show the 'Set your "
            "starting cash' dashboard card -- no separate flag to check."
        ),
    )
    opening_date: date
    cash_on_hand: Decimal
    total_debt_out: Decimal
    logo_url: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class BusinessDetailOut(BusinessOut):
    """Business plus its active subscription (the GET /businesses/{id} payload)."""

    active_subscription: SubscriptionOut | None = None


class SubscriptionPlanOut(BaseModel):
    """A row from ``subscription_plans`` (FREE/STARTER/PRO) -- GET /subscription-plans."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    label: str
    engine_phase_max: int
    price_monthly: Decimal
    currency_id: int | None
    is_active: bool


class SubscriptionChangeRequest(BaseModel):
    """POST /businesses/{id}/subscription -- switch the business to a
    different plan. The previous subscription is deactivated (not deleted)
    and a new ``business_subscriptions`` row is created, preserving history."""

    plan_id: int = Field(..., ge=1)


class SubscriptionListOut(BaseModel):
    items: list[SubscriptionOut]


__all__ = [
    "BusinessCreateRequest",
    "BusinessUpdateRequest",
    "SetOpeningBalanceRequest",
    "SubscriptionOut",
    "SubscriptionPlanOut",
    "SubscriptionChangeRequest",
    "SubscriptionListOut",
    "CurrencyOut",
    "CountryOut",
    "BusinessOut",
    "BusinessDetailOut",
    "BusinessStatus",
]
