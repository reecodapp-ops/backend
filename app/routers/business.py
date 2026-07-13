"""Business Setup router.

Endpoints (per blueprint Section 3, F2):
    POST  /businesses          create + onboard (opening balance, FREE plan)
    PATCH /businesses/{id}      update profile / settings (owner-only)
    GET   /businesses/{id}      read business + active subscription (owner-only)

Subscriptions:
    GET  /subscription-plans                  list available plans (see routers/lookups.py)
    GET  /businesses/{id}/subscriptions        subscription history (owner-only)
    POST /businesses/{id}/subscription         change plan (owner-only)

Insights:
    GET  /businesses/{id}/insights             smart business insights (owner-only)
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.core.dependencies import BusinessServiceDep, CurrentUser, InsightsServiceDep
from app.schemas.business import (
    BusinessCreateRequest,
    BusinessDetailOut,
    BusinessOut,
    BusinessUpdateRequest,
    SetOpeningBalanceRequest,
    SubscriptionChangeRequest,
    SubscriptionListOut,
    SubscriptionOut,
)
from app.schemas.insights import InsightsListOut

router = APIRouter(prefix="/businesses", tags=["Business Setup"])


@router.post(
    "",
    response_model=BusinessDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create and onboard a business (opening balance + FREE plan)",
)
async def create_business(
    payload: BusinessCreateRequest,
    service: BusinessServiceDep,
    current_user: CurrentUser,
) -> BusinessDetailOut:
    return await service.create(current_user, payload)


@router.patch(
    "/{business_id}",
    response_model=BusinessOut,
    status_code=status.HTTP_200_OK,
    summary="Update business profile / settings",
)
async def update_business(
    business_id: uuid.UUID,
    payload: BusinessUpdateRequest,
    service: BusinessServiceDep,
    current_user: CurrentUser,
) -> BusinessOut:
    return await service.update(current_user, business_id, payload)


@router.get(
    "/{business_id}",
    response_model=BusinessDetailOut,
    status_code=status.HTTP_200_OK,
    summary="Get a business with its active subscription",
)
async def get_business(
    business_id: uuid.UUID,
    service: BusinessServiceDep,
    current_user: CurrentUser,
) -> BusinessDetailOut:
    return await service.get(current_user, business_id)


@router.post(
    "/{business_id}/opening-balance",
    response_model=BusinessOut,
    summary="Set opening cash after skipping the onboarding wizard step (SRS Section 6 nudge)",
)
async def set_opening_balance(
    business_id: uuid.UUID,
    payload: SetOpeningBalanceRequest,
    service: BusinessServiceDep,
    current_user: CurrentUser,
) -> BusinessOut:
    return await service.set_opening_balance(current_user, business_id, payload)


@router.get(
    "/{business_id}/subscriptions",
    response_model=SubscriptionListOut,
    summary="Subscription history for this business, newest first",
)
async def list_business_subscriptions(
    business_id: uuid.UUID,
    service: BusinessServiceDep,
    current_user: CurrentUser,
) -> SubscriptionListOut:
    return await service.list_subscription_history(current_user, business_id)


@router.post(
    "/{business_id}/subscription",
    response_model=SubscriptionOut,
    summary="Change the business's subscription plan",
)
async def change_business_subscription(
    business_id: uuid.UUID,
    payload: SubscriptionChangeRequest,
    service: BusinessServiceDep,
    current_user: CurrentUser,
) -> SubscriptionOut:
    return await service.change_subscription(current_user, business_id, payload)


@router.get(
    "/{business_id}/insights",
    response_model=InsightsListOut,
    summary="Smart business insights — recommendations and warnings for the owner",
)
async def get_business_insights(
    business_id: uuid.UUID,
    service: InsightsServiceDep,
    current_user: CurrentUser,
) -> InsightsListOut:
    return await service.get_insights(current_user, business_id)
