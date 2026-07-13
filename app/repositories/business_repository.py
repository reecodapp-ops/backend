"""Business repository — all database access for businesses, subscriptions,
opening-balance capital transactions, and the lookup tables consulted during
onboarding (business_types, currencies, subscription_plans,
capital_transaction_types).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.business import (
    Business,
    BusinessSubscription,
    CapitalTransaction,
)
from app.models.lookups import (
    BusinessType,
    CapitalTransactionType,
    Country,
    Currency,
    SubscriptionPlan,
)

OPENING_BALANCE_CODE = "OPENING_BALANCE"
FREE_PLAN_CODE = "FREE"


class BusinessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----- lookups ---------------------------------------------------------- #
    async def get_business_type(self, type_id: int) -> BusinessType | None:
        result = await self._session.execute(
            select(BusinessType).where(BusinessType.id == type_id)
        )
        return result.scalar_one_or_none()

    async def get_plan_by_code(self, code: str) -> SubscriptionPlan | None:
        result = await self._session.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.code == code)
        )
        return result.scalar_one_or_none()

    async def get_capital_type_by_code(
        self, code: str
    ) -> CapitalTransactionType | None:
        result = await self._session.execute(
            select(CapitalTransactionType).where(
                CapitalTransactionType.code == code
            )
        )
        return result.scalar_one_or_none()

    # ----- currencies [NEW v3] ------------------------------------------------ #
    async def get_currency(self, currency_id: int) -> Currency | None:
        result = await self._session.execute(
            select(Currency).where(Currency.id == currency_id)
        )
        return result.scalar_one_or_none()

    async def get_currency_by_iso_code(self, iso_code: str) -> Currency | None:
        result = await self._session.execute(
            select(Currency).where(Currency.iso_code == iso_code.upper())
        )
        return result.scalar_one_or_none()

    async def list_active_currencies(self) -> list[Currency]:
        result = await self._session.execute(
            select(Currency)
            .where(Currency.is_active.is_(True))
            .order_by(Currency.sort_order.asc(), Currency.iso_code.asc())
        )
        return list(result.scalars().all())

    # ----- countries [NEW v4] -------------------------------------------------- #
    async def get_country(self, country_id: int) -> Country | None:
        result = await self._session.execute(
            select(Country).where(Country.id == country_id)
        )
        return result.scalar_one_or_none()

    async def list_active_countries(self) -> list[Country]:
        result = await self._session.execute(
            select(Country)
            .where(Country.is_active.is_(True))
            .order_by(Country.sort_order.asc(), Country.name.asc())
        )
        return list(result.scalars().all())

    # ----- businesses ------------------------------------------------------- #
    async def get_by_id(self, business_id: uuid.UUID) -> Business | None:
        result = await self._session.execute(
            select(Business)
            .where(Business.id == business_id)
            .options(selectinload(Business.currency), selectinload(Business.country_ref))
        )
        return result.scalar_one_or_none()

    async def list_for_owner(self, owner_id: uuid.UUID) -> list[Business]:
        result = await self._session.execute(
            select(Business)
            .where(Business.owner_id == owner_id)
            .options(selectinload(Business.currency), selectinload(Business.country_ref))
            .order_by(Business.created_at.asc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        business_type_id: int,
        shop_name: str,
        phone_number: str | None,
        location: str | None,
        street: str | None = None,
        city: str | None = None,
        district: str | None = None,
        country: str | None = None,
        country_id: int | None = None,
        formatted_address: str | None = None,
        latitude: Decimal | None = None,
        longitude: Decimal | None = None,
        place_id: str | None = None,
        currency_id: int,
        opening_balance: Decimal | None,
        opening_balance_set_at: datetime | None,
        opening_date: date,
    ) -> Business:
        business = Business(
            owner_id=owner_id,
            business_type_id=business_type_id,
            shop_name=shop_name,
            phone_number=phone_number,
            location=location,
            street=street,
            city=city,
            district=district,
            country=country,
            country_id=country_id,
            formatted_address=formatted_address,
            latitude=latitude,
            longitude=longitude,
            place_id=place_id,
            currency_id=currency_id,
            opening_balance=opening_balance or Decimal("0"),
            opening_balance_set_at=opening_balance_set_at,
            opening_date=opening_date,
            # Opening balance seeds the cash position directly (see service note).
            cash_on_hand=opening_balance or Decimal("0"),
            total_debt_out=Decimal("0"),
            status="ACTIVE",
        )
        self._session.add(business)
        await self._session.flush()
        await self._session.refresh(business)
        # Ensure `business.currency` is loaded (needed by BusinessOut/
        # currency_code / currency_symbol) without a second round-trip surprise
        # later — refresh() only reloads columns, not relationships.
        result = await self._session.execute(
            select(Business)
            .where(Business.id == business.id)
            .options(selectinload(Business.currency), selectinload(Business.country_ref))
        )
        return result.scalar_one()

    async def add_opening_balance_txn(
        self,
        *,
        business_id: uuid.UUID,
        capital_type_id: int,
        amount: Decimal,
        occurred_at: datetime,
    ) -> CapitalTransaction:
        txn = CapitalTransaction(
            business_id=business_id,
            type_id=capital_type_id,
            source_type_id=None,
            amount=amount,
            occurred_at=occurred_at,
            note="Opening balance at business setup",
            is_correction=False,
        )
        self._session.add(txn)
        await self._session.flush()
        return txn

    async def assign_subscription(
        self, *, business_id: uuid.UUID, plan_id: int
    ) -> BusinessSubscription:
        sub = BusinessSubscription(
            business_id=business_id,
            plan_id=plan_id,
            started_at=datetime.now(timezone.utc),
            expires_at=None,
            is_active=True,
        )
        self._session.add(sub)
        await self._session.flush()
        await self._session.refresh(sub)
        return sub

    async def get_active_subscription(
        self, business_id: uuid.UUID
    ) -> BusinessSubscription | None:
        result = await self._session.execute(
            select(BusinessSubscription)
            .where(
                BusinessSubscription.business_id == business_id,
                BusinessSubscription.is_active.is_(True),
            )
            .order_by(BusinessSubscription.started_at.desc())
        )
        return result.scalars().first()

    async def get_plan_by_id(self, plan_id: int) -> SubscriptionPlan | None:
        result = await self._session.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id)
        )
        return result.scalar_one_or_none()

    async def list_active_plans(self) -> list[SubscriptionPlan]:
        result = await self._session.execute(
            select(SubscriptionPlan)
            .where(SubscriptionPlan.is_active.is_(True))
            .order_by(SubscriptionPlan.price_monthly.asc())
        )
        return list(result.scalars().all())

    async def list_subscription_history(
        self, business_id: uuid.UUID
    ) -> list[BusinessSubscription]:
        result = await self._session.execute(
            select(BusinessSubscription)
            .where(BusinessSubscription.business_id == business_id)
            .order_by(BusinessSubscription.started_at.desc())
        )
        return list(result.scalars().all())

    async def deactivate_subscription(self, subscription: BusinessSubscription) -> None:
        subscription.is_active = False
        subscription.expires_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def flush(self) -> None:
        await self._session.flush()
