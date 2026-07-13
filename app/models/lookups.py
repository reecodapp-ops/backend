"""Lookup models for business setup.

These mirror the seeded lookup tables in dukamate_schema_v4.sql:
- ``business_types``           (Retail shop, Boutique, ... seeded)
- ``currencies``                (ISO 4217 currency master list, seeded; NEW v3)
- ``countries``                 (ISO 3166-1 country master list, seeded; NEW v4)
- ``subscription_plans``       (FREE, STARTER, PRO seeded)
- ``capital_transaction_types``(OPENING_BALANCE, MONEY_IN, ... seeded)

They are read-mostly; the backend never creates rows here (seeded once), with
the exception of ``currencies``/``countries`` which may grow over time — new
rows are added by plain INSERT and are immediately usable by every business
without any code change.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, updated_at_column


class BusinessType(Base):
    __tablename__ = "business_types"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    icon_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Currency(Base):
    """ISO 4217 currency master list — mirrors the ``currencies`` table (NEW v3).

    Replaces the old ``businesses.currency_code`` / ``currency_symbol`` free-text
    pair. Every business and subscription plan now references a row here via
    ``currency_id``. Adding support for a new market is a plain INSERT into this
    table — zero code or schema changes required, and every currency already in
    the table is immediately usable everywhere currency is referenced.
    """

    __tablename__ = "currencies"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    iso_code: Mapped[str] = mapped_column(String(3), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    decimal_places: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("2"), default=2
    )
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    exchange_rate_to_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"), default=True
    )
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Currency {self.iso_code} {self.symbol!r}>"


class Country(Base):
    """ISO 3166-1 country master list — mirrors ``countries`` (NEW v4;
    ``calling_code`` + full 242-country seed added by the country-phone-codes
    migration).

    Decoupled from ``currencies`` on purpose: several countries share one
    currency (XOF/XAF), so currency must never be derived from country (or
    vice versa) at query time. ``default_currency_id`` is only a *suggestion*
    applied once, client-side, at onboarding's currency-selection step — the
    business's actual ``currency_id`` stays independent thereafter (GEO-3).
    Also drives map center for any location feature.

    ``calling_code`` (E.164 prefix, e.g. "+256") powers the country-code
    picker on every phone-number field in the app (business, customer,
    supplier). It has nothing to do with currency or map center — a country
    can have a calling code with no currency mapped yet (all 230 non-target-
    market countries currently have ``default_currency_id IS NULL``, see the
    migration's own note on why that's deliberate).
    """

    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    iso_code: Mapped[str] = mapped_column(String(2), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    calling_code: Mapped[str] = mapped_column(String(6), nullable=False)
    default_currency_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("currencies.id"), nullable=True
    )
    map_center_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    map_center_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    map_default_zoom: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("6"), default=6
    )
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"), default=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Country {self.iso_code} {self.name!r}>"


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    engine_phase_max: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1
    )
    price_monthly: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0")
    )
    # [CHANGED v3] currency_code (free text) -> currency_id FK. Nullable in the
    # schema (a plan priced at 0 need not carry a currency), so kept optional
    # here too.
    currency_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("currencies.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CapitalTransactionType(Base):
    __tablename__ = "capital_transaction_types"
    __table_args__ = (
        CheckConstraint("direction IN ('I', 'O')", name="capital_txn_type_dir_check"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    direction: Mapped[str] = mapped_column(String(1), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class PaymentType(Base):
    """Lookup: how a sale was paid — CASH ('Paid now') or ON_DEBT ('On credit').

    Seeded once in the schema. The Sales module reads this to discriminate cash
    vs debt sales and to let the DB triggers apply the correct aggregate updates.
    """

    __tablename__ = "payment_types"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class CapitalSourceType(Base):
    """Seeded lookup: where injected capital came from (PERSONAL_SAVINGS,
    BANK_LOAN, MOBILE_LOAN, FAMILY_FRIEND, INVESTOR, OTHER).

    Only meaningful for MONEY_IN capital transactions; MONEY_OUT / PERSONAL_USE
    leave source_type_id NULL.
    """

    __tablename__ = "capital_source_types"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
