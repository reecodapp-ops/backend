"""Business and subscription models — mirror dukamate_schema_v4.sql.

``businesses`` is the tenant root. ``cash_on_hand`` and ``total_debt_out`` are
cached aggregates maintained by DB triggers; the backend reads them and does not
write them directly (except the initial opening balance flow, see BusinessService).

CURRENCY [CHANGED v3]
---------------------
``currency_code`` / ``currency_symbol`` free-text columns are gone. Every
business now carries a ``currency_id`` FK into the ``currencies`` lookup table.
The ``currency`` relationship is eager-loaded (``lazy="selectin"``) because it
is read on virtually every business fetch (dashboard header, receipts, etc.),
and the ``currency_code`` / ``currency_symbol`` *properties* below proxy onto it
so existing response schemas/consumers that read those attribute names keep
working without change.

LOCATION [ADD v3] / COUNTRY [ADD v4]
--------------------------------------
Structured location fields (street/city/district/formatted_address/latitude/
longitude/place_id) sit alongside the legacy free-text ``location`` and
``country`` columns, kept for backward compatibility per the SQL schema
comments. ``country_id`` (NEW v4) is the logic-bearing FK — decoupled from
``currency_id`` on purpose (see app/models/lookups.py::Country) — while
``country`` (free text) stays a display-only fallback. New writes should
prefer ``country_id``.

ONBOARDING & IDENTITY SRS v1.0 — SCOPE NOTE
----------------------------------------------
Per explicit product direction, this delivery implements GEO-1..GEO-3 (the
countries table + country_id) and the dashboard first-run nudge
(``opening_balance_set_at``), but *not* BIZ-ASSOC-1..4 (the mandatory
create/join wizard branch, invite codes, multi-user ``business_members``
enforcement). ``owner_id`` remains the sole authorization source for now —
deferred for speed, see MIGRATION_NOTES.md. Language selection (GEO-4) needs
no backend work at all — it's handled entirely client-side via i18next, per
product direction; there is deliberately no ``languages`` table or
``users.language_id`` column.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    created_at_column,
    updated_at_column,
    uuid_pk,
)
from app.models.lookups import Country, Currency


class Business(Base):
    __tablename__ = "businesses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'ARCHIVED')",
            name="businesses_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    business_type_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("business_types.id"), nullable=False
    )
    shop_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ----- Location -----
    # Legacy free-text field, kept for backward compatibility (pre-v3 records
    # and any client not yet upgraded to the structured fields).
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Structured location [NEW v3] — supports map pin, navigation, delivery.
    street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    district: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Legacy free-text country name — backward-compat display fallback only
    # (SRS GEO-2). Logic must use country_id below, never this column.
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # [NEW v4] The logic-bearing FK. Decoupled from currency_id on purpose —
    # see app/models/lookups.py::Country docstring (GEO-1/GEO-3).
    country_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("countries.id"), nullable=True
    )
    country_ref: Mapped["Country | None"] = relationship(lazy="selectin")
    formatted_address: Mapped[str | None] = mapped_column(String(400), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    place_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ----- Currency [CHANGED v3] -----
    currency_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("currencies.id"), nullable=False
    )
    currency: Mapped["Currency"] = relationship(lazy="selectin")

    opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    # [NEW v4] NULL until an owner explicitly sets opening cash (wizard step 5,
    # skippable, or POST /businesses/{id}/opening-balance later). Powers the
    # "Set your starting cash" dashboard nudge (SRS Section 6) with no
    # separate onboarding_completed flag — see BusinessService.
    opening_balance_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opening_date: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=text("CURRENT_DATE")
    )
    # Cached aggregates (maintained by triggers)
    cash_on_hand: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    total_debt_out: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'"), default="ACTIVE"
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    subscriptions: Mapped[list["BusinessSubscription"]] = relationship(
        back_populates="business",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    # ----- Backward-compatible accessors -----
    # Proxy the old attribute names onto the related Currency row so schemas,
    # services, and any external code that still reads `business.currency_code`
    # / `business.currency_symbol` keep working unchanged. `currency` is
    # eager-loaded (lazy="selectin") so these never trigger a lazy-load error
    # under AsyncSession.
    @property
    def currency_code(self) -> str | None:
        return self.currency.iso_code if self.currency is not None else None

    @property
    def currency_symbol(self) -> str | None:
        return self.currency.symbol if self.currency is not None else None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Business id={self.id} name={self.shop_name!r}>"


class BusinessSubscription(Base):
    __tablename__ = "business_subscriptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("subscription_plans.id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_column()

    business: Mapped["Business"] = relationship(back_populates="subscriptions")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BusinessSubscription business={self.business_id} plan={self.plan_id}>"


class CapitalTransaction(Base):
    """Minimal mapping needed for the opening-balance write during onboarding.

    The full Capital module (Money Added / Removed) is implemented later; this
    model exists now so BusinessService can create the OPENING_BALANCE row in the
    same transaction as the business, per the blueprint.
    """

    __tablename__ = "capital_transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="capital_txn_amount_check"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    type_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("capital_transaction_types.id"), nullable=False
    )
    source_type_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    is_correction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    original_txn_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("capital_transactions.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
