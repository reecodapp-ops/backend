"""Business Setup service.

Implements onboarding, settings update, and detail read.

OPENING BALANCE — IMPORTANT DESIGN NOTE
---------------------------------------
The blueprint describes onboarding as "business + OPENING_BALANCE capital row +
FREE subscription" in one transaction. However, the shipped schema double-counts
the opening balance if it is recorded BOTH as ``businesses.opening_balance`` AND
as an ``OPENING_BALANCE`` capital_transaction:

  * ``fn_after_capital_txn_insert`` adds every direction='I' capital row to
    ``cash_on_hand`` (OPENING_BALANCE has direction 'I').
  * ``reconcile_business_cash`` computes
    ``cash = opening_balance + SUM(direction='I' capital) - outflows`` —
    summing the OPENING_BALANCE row again on top of the opening_balance column.

To keep ``cash_on_hand`` correct under both the live trigger and the nightly
reconciliation, the opening balance is recorded ONCE, as the
``businesses.opening_balance`` column (which also seeds ``cash_on_hand``). We do
NOT insert an OPENING_BALANCE capital_transaction. This matches the
reconcile_business_cash formula exactly and avoids silent drift.

(If the schema's reconcile function is later changed to exclude OPENING_BALANCE
from the inflow sum, an explicit capital row could be added; until then this is
the only way to stay consistent.)

CURRENCY [CHANGED v3]
---------------------
Currency is no longer a free-text code/symbol pair supplied by the client. The
client sends ``currency_id`` referencing a row in the ``currencies`` table; the
service validates that the row exists and is active, and everything downstream
(``business.currency_code`` / ``business.currency_symbol``) is derived
dynamically from that row via the model's backward-compatible properties.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.phone import resolve_phone_number
from app.models.business import Business
from app.models.user import User
from app.repositories.business_repository import (
    FREE_PLAN_CODE,
    BusinessRepository,
)
from app.schemas.business import (
    BusinessCreateRequest,
    BusinessDetailOut,
    BusinessOut,
    BusinessUpdateRequest,
    SetOpeningBalanceRequest,
    SubscriptionChangeRequest,
    SubscriptionListOut,
    SubscriptionOut,
    SubscriptionPlanOut,
)


class BusinessService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = BusinessRepository(session)

    # ----- create / onboarding --------------------------------------------- #
    async def create(
        self, owner: User, data: BusinessCreateRequest
    ) -> BusinessDetailOut:
        # Validate the business type exists and is usable.
        biz_type = await self._repo.get_business_type(data.business_type_id)
        if biz_type is None:
            raise ValidationError(
                "Unknown business_type_id.", code="invalid_business_type"
            )
        if not biz_type.is_active:
            raise ValidationError(
                "Selected business type is not available.",
                code="inactive_business_type",
            )

        # Currency must exist and be active (dynamic lookup — never hardcoded).
        currency = await self._repo.get_currency(data.currency_id)
        if currency is None:
            raise ValidationError(
                "Unknown currency_id.", code="invalid_currency"
            )
        if not currency.is_active:
            raise ValidationError(
                "Selected currency is not available.",
                code="inactive_currency",
            )

        # FREE plan must be seeded.
        free_plan = await self._repo.get_plan_by_code(FREE_PLAN_CODE)
        if free_plan is None:
            raise ValidationError(
                "Subscription plans are not configured.",
                code="plans_not_seeded",
            )

        # Country is optional but must be valid if given (GEO-1/GEO-2). It is
        # never used to derive currency_id -- the two stay independent.
        if data.country_id is not None:
            country = await self._repo.get_country(data.country_id)
            if country is None:
                raise ValidationError(
                    "Unknown country_id.", code="invalid_country"
                )
            if not country.is_active:
                raise ValidationError(
                    "Selected country is not available.", code="inactive_country"
                )

        opening_date = data.opening_date or date.today()
        # `None` means the owner skipped wizard step 5 (Onboarding SRS
        # Section 3) -- opening_balance_set_at stays NULL so the dashboard
        # nudge (Section 6) shows up until they set it, here or later via
        # set_opening_balance().
        opening_balance_set_at = (
            datetime.now(timezone.utc) if data.opening_balance is not None else None
        )

        phone_number = await resolve_phone_number(
            self._session,
            phone_country_id=data.phone_country_id,
            phone_local_number=data.phone_local_number,
            legacy_phone_number=data.phone_number,
        )

        # Single transaction (committed by the get_db dependency on success):
        #   1. create business (cash_on_hand seeded from opening_balance)
        #   2. assign FREE subscription
        business = await self._repo.create(
            owner_id=owner.id,
            business_type_id=data.business_type_id,
            shop_name=data.shop_name,
            phone_number=phone_number,
            location=data.location,
            street=data.street,
            city=data.city,
            district=data.district,
            country=data.country,
            country_id=data.country_id,
            formatted_address=data.formatted_address,
            latitude=data.latitude,
            longitude=data.longitude,
            place_id=data.place_id,
            currency_id=data.currency_id,
            opening_balance=data.opening_balance,
            opening_balance_set_at=opening_balance_set_at,
            opening_date=opening_date,
        )

        # The business now exists. Set the RLS context to its id before writing
        # the subscription, because business_subscriptions is RLS-protected and
        # its biz_isolation policy (USING, applied as the INSERT check) requires
        # app.current_business_id to match the row's business_id. Onboarding is
        # the one flow that creates a business and its first child row in the
        # same request, so the context must be set here rather than by the
        # business-scoping dependency (which only runs on X-Business-Id routes).
        await self._set_rls_business(business.id)

        subscription = await self._repo.assign_subscription(
            business_id=business.id, plan_id=free_plan.id
        )

        detail = BusinessDetailOut.model_validate(business)
        detail.active_subscription = SubscriptionOut.model_validate(subscription)
        return detail

    # ----- update / settings ------------------------------------------------ #
    async def update(
        self, owner: User, business_id: uuid.UUID, data: BusinessUpdateRequest
    ) -> BusinessOut:
        business = await self._get_owned(owner, business_id)

        if data.business_type_id is not None:
            biz_type = await self._repo.get_business_type(data.business_type_id)
            if biz_type is None:
                raise ValidationError(
                    "Unknown business_type_id.", code="invalid_business_type"
                )
            business.business_type_id = data.business_type_id

        if data.currency_id is not None:
            currency = await self._repo.get_currency(data.currency_id)
            if currency is None:
                raise ValidationError(
                    "Unknown currency_id.", code="invalid_currency"
                )
            if not currency.is_active:
                raise ValidationError(
                    "Selected currency is not available.",
                    code="inactive_currency",
                )
            business.currency_id = data.currency_id
            business.currency = currency

        if data.shop_name is not None:
            business.shop_name = data.shop_name
        if data.phone_country_id is not None:
            business.phone_number = await resolve_phone_number(
                self._session,
                phone_country_id=data.phone_country_id,
                phone_local_number=data.phone_local_number,
                legacy_phone_number=data.phone_number,
            )
        elif data.phone_number is not None:
            business.phone_number = data.phone_number

        if data.location is not None:
            business.location = data.location
        if data.street is not None:
            business.street = data.street
        if data.city is not None:
            business.city = data.city
        if data.district is not None:
            business.district = data.district
        if data.country is not None:
            business.country = data.country
        if data.country_id is not None:
            country = await self._repo.get_country(data.country_id)
            if country is None:
                raise ValidationError(
                    "Unknown country_id.", code="invalid_country"
                )
            business.country_id = data.country_id
            business.country_ref = country
        if data.formatted_address is not None:
            business.formatted_address = data.formatted_address
        if data.latitude is not None:
            business.latitude = data.latitude
        if data.longitude is not None:
            business.longitude = data.longitude
        if data.place_id is not None:
            business.place_id = data.place_id

        if data.logo_url is not None:
            business.logo_url = data.logo_url
        if data.status is not None:
            business.status = data.status

        business.updated_at = datetime.now(timezone.utc)
        await self._repo.flush()
        await self._session.refresh(business)
        return BusinessOut.model_validate(business)

    # ----- read -------------------------------------------------------------- #
    async def get(self, owner: User, business_id: uuid.UUID) -> BusinessDetailOut:
        business = await self._get_owned(owner, business_id)
        await self._set_rls_business(business.id)
        detail = BusinessDetailOut.model_validate(business)
        sub = await self._repo.get_active_subscription(business.id)
        detail.active_subscription = (
            SubscriptionOut.model_validate(sub) if sub else None
        )
        return detail

    # ----- subscriptions ------------------------------------------------------ #
    async def list_plans(self) -> list[SubscriptionPlanOut]:
        rows = await self._repo.list_active_plans()
        return [SubscriptionPlanOut.model_validate(p) for p in rows]

    # ----- countries [NEW v4] --------------------------------------------------- #
    async def list_countries(self):
        from app.schemas.business import CountryOut

        rows = await self._repo.list_active_countries()
        return [CountryOut.model_validate(c) for c in rows]

    # ----- opening balance nudge [NEW v4] --------------------------------------- #
    async def set_opening_balance(
        self, owner: User, business_id: uuid.UUID, data: SetOpeningBalanceRequest
    ) -> BusinessOut:
        """POST /businesses/{id}/opening-balance -- lets an owner who skipped
        wizard step 5 set it later, from the dashboard nudge card (SRS
        Section 6). Also seeds cash_on_hand the same way onboarding does, so
        the two entry points stay consistent."""
        business = await self._get_owned(owner, business_id)
        if business.opening_balance_set_at is not None:
            raise ValidationError(
                "Opening balance has already been set for this business.",
                code="opening_balance_already_set",
            )
        business.opening_balance = data.opening_balance
        business.cash_on_hand = business.cash_on_hand + data.opening_balance
        business.opening_balance_set_at = datetime.now(timezone.utc)
        business.updated_at = datetime.now(timezone.utc)
        await self._repo.flush()
        await self._session.refresh(business)
        return BusinessOut.model_validate(business)

    async def list_subscription_history(
        self, owner: User, business_id: uuid.UUID
    ) -> SubscriptionListOut:
        await self._get_owned(owner, business_id)
        rows = await self._repo.list_subscription_history(business_id)
        return SubscriptionListOut(items=[SubscriptionOut.model_validate(s) for s in rows])

    async def change_subscription(
        self, owner: User, business_id: uuid.UUID, data: SubscriptionChangeRequest
    ) -> SubscriptionOut:
        """Switch to a different plan: deactivate the current active
        subscription (if any) and create a new one. History is preserved —
        old rows are never deleted, only ``is_active`` is flipped."""
        business = await self._get_owned(owner, business_id)
        plan = await self._repo.get_plan_by_id(data.plan_id)
        if plan is None:
            raise ValidationError("Unknown plan_id.", code="invalid_plan")
        if not plan.is_active:
            raise ValidationError(
                "Selected plan is not available.", code="inactive_plan"
            )

        await self._set_rls_business(business.id)
        current = await self._repo.get_active_subscription(business.id)
        if current is not None:
            if current.plan_id == plan.id:
                raise ValidationError(
                    "This business is already on this plan.",
                    code="plan_unchanged",
                )
            await self._repo.deactivate_subscription(current)

        new_sub = await self._repo.assign_subscription(
            business_id=business.id, plan_id=plan.id
        )
        return SubscriptionOut.model_validate(new_sub)

    # ----- helpers ----------------------------------------------------------- #
    async def get_authorized_business(
        self, owner: User, business_id: uuid.UUID
    ) -> Business:
        """Verify ``owner`` owns ``business_id``, set the PostgreSQL RLS
        session variable for it, and return the loaded ``Business``.

        Public (unlike ``_get_owned``/``_set_rls_business``) so other
        services that operate on a single business by id+owner rather than
        via the X-Business-Id-header ``BusinessContext`` — e.g.
        ``InsightsService`` — can reuse the exact same ownership/RLS gate
        instead of duplicating it.
        """
        business = await self._get_owned(owner, business_id)
        await self._set_rls_business(business.id)
        return business

    async def _get_owned(self, owner: User, business_id: uuid.UUID) -> Business:
        business = await self._repo.get_by_id(business_id)
        if business is None:
            raise NotFoundError("Business not found.", code="business_not_found")
        if business.owner_id != owner.id:
            # Ownership guard mirrors the DB-level RLS isolation.
            raise ForbiddenError(
                "You do not have access to this business.",
                code="business_not_owned",
            )
        return business

    async def _set_rls_business(self, business_id: uuid.UUID) -> None:
        """Set app.current_business_id for the current transaction (PostgreSQL).

        No-op on non-PostgreSQL backends (e.g. SQLite in tests).
        """
        bind = self._session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            await self._session.execute(
                text("SELECT set_config('app.current_business_id', :bid, true)"),
                {"bid": str(business_id)},
            )
