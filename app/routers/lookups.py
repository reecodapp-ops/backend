"""Read-only lookup endpoints that are not tied to a specific business.

    GET /currencies           -> active rows from the `currencies` table (NEW v3)
    GET /countries            -> active rows from the `countries` table (NEW v4;
                                  calling_code + full 242-country seed added by
                                  the country-phone-codes migration)
    GET /subscription-plans   -> active rows from `subscription_plans` (FREE/STARTER/PRO)
    GET /phone-numbers/parse  -> split an E.164 number back into country + local
                                  parts, for a frontend that would rather not
                                  duplicate `phonenumbers` parsing logic itself

All loaded dynamically from the database rather than hardcoded. Requires
authentication only (no X-Business-Id -- these are used before a business
exists, e.g. during onboarding, as well as in business settings).
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.dependencies import BusinessServiceDep, CurrentUser, DbSession
from app.core.phone import parse_phone_number
from app.repositories.business_repository import BusinessRepository
from app.schemas.business import CountryOut, CurrencyOut, SubscriptionPlanOut

router = APIRouter(prefix="/currencies", tags=["Lookups"])
countries_router = APIRouter(prefix="/countries", tags=["Lookups"])
plans_router = APIRouter(prefix="/subscription-plans", tags=["Lookups"])
phone_numbers_router = APIRouter(prefix="/phone-numbers", tags=["Lookups"])


class PhoneNumberParseOut(BaseModel):
    """A phone number split back into its parts, e.g. for pre-filling an
    edit form's country picker + local-number input from a stored E.164
    string."""

    calling_code: str
    iso_code: str
    local_number: str
    e164: str


@router.get("", response_model=list[CurrencyOut])
async def list_currencies(
    session: DbSession,
    current_user: CurrentUser,
) -> list[CurrencyOut]:
    repo = BusinessRepository(session)
    currencies = await repo.list_active_currencies()
    return [CurrencyOut.model_validate(c) for c in currencies]


@countries_router.get("", response_model=list[CountryOut])
async def list_countries(
    service: BusinessServiceDep,
    current_user: CurrentUser,
) -> list[CountryOut]:
    """Onboarding wizard's country picker (SRS step 2) AND the phone-number
    country-code picker used everywhere a phone number is entered (business,
    customer, supplier). Selecting a country only *suggests* a currency/
    map-center client-side via ``default_currency_id`` /
    ``map_center_lat``/``map_center_lng`` -- the backend never derives
    currency from country or vice versa. ``calling_code`` is what powers the
    phone picker specifically and is populated for all 242 rows."""
    return await service.list_countries()


@plans_router.get("", response_model=list[SubscriptionPlanOut])
async def list_subscription_plans(
    service: BusinessServiceDep,
    current_user: CurrentUser,
) -> list[SubscriptionPlanOut]:
    return await service.list_plans()


@phone_numbers_router.get("/parse", response_model=PhoneNumberParseOut)
async def parse_phone(
    current_user: CurrentUser,
    number: str = Query(..., description="A stored E.164 phone number, e.g. +256700111222"),
) -> PhoneNumberParseOut:
    """Optional helper: splits a stored E.164 phone number back into a
    calling code + national-format local number, so an edit form's country
    picker + local-number input can be pre-filled without the frontend
    duplicating `phonenumbers` parsing logic itself. Raises
    ``422 invalid_phone_number`` for anything that isn't a valid E.164
    number."""
    result = parse_phone_number(number)
    return PhoneNumberParseOut(**result)
