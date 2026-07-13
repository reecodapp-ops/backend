"""Customer router (per blueprint F3 + the Customer Credibility flagship
feature). All routes require the X-Business-Id header.

    POST   /customers                      create
    GET    /customers                      search/list (filterable — see below)
    GET    /customers/high-risk            credit board: HIGH_RISK customers
    GET    /customers/excellent            credit board: EXCELLENT customers
    GET    /customers/recommendation       credit board: all customers, filterable
    GET    /customers/credit-history       Decision Engine credit event timeline
    GET    /customers/{id}                 detail
    GET    /customers/{id}/credit          full credit profile
    GET    /customers/{id}/ledger          debt ledger (per-sale debt history)
    GET    /customers/{id}/payments        payment + allocation history
    PATCH  /customers/{id}                 update (incl. status=ARCHIVED)
    POST   /customers/{id}/archive         archive (soft delete)
    POST   /customers/{id}/simulate-credit "would this debt sale be approved?"

ROUTE ORDERING NOTE: FastAPI/Starlette matches path templates in registration
order. The literal-segment routes (``/high-risk``, ``/excellent``,
``/recommendation``, ``/credit-history``) are declared BEFORE
``/{customer_id}`` so they are never swallowed by the dynamic path parameter.

This controller is intentionally thin: every credibility/decision computation
lives in ``CustomerService`` (see its module docstring). Routes here only
parse query params and delegate.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.core.master_data_deps import CustomerServiceDep, ScoringServiceDep
from app.schemas.customer import (
    CreditEventListOut,
    CreditSimulationOut,
    CreditSimulationRequest,
    CustomerCreateRequest,
    CustomerCreditListOut,
    CustomerCreditOut,
    CustomerLedgerListOut,
    CustomerListOut,
    CustomerOut,
    CustomerPaymentHistoryListOut,
    CustomerUpdateRequest,
    LedgerStatus,
)
from app.schemas.scoring import CustomerScoresOut, CustomerViewMode

router = APIRouter(prefix="/customers", tags=["Customers"])

CreditLevelQuery = Literal["UNSCORED", "BLOCKED", "HIGH_RISK", "AVERAGE", "GOOD", "EXCELLENT"]


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
async def create_customer(
    payload: CustomerCreateRequest, service: CustomerServiceDep
) -> CustomerOut:
    return await service.create(payload)


@router.get("", response_model=CustomerListOut)
async def search_customers(
    service: CustomerServiceDep,
    search: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[
        Literal["ACTIVE", "ARCHIVED"] | None, Query(alias="status")
    ] = "ACTIVE",
    order: Annotated[
        Literal["debt_desc", "name_asc", "recent", "score_asc", "score_desc"],
        Query(),
    ] = "recent",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    credit_level: Annotated[
        CreditLevelQuery | None,
        Query(description="Filter: UNSCORED/BLOCKED/HIGH_RISK/AVERAGE/GOOD/EXCELLENT"),
    ] = None,
    is_blocked: Annotated[
        bool | None, Query(description="Filter: is_credit_blocked")
    ] = None,
    has_active_debt: Annotated[
        bool | None,
        Query(description="true = debt_balance > 0, false = debt_balance <= 0"),
    ] = None,
    min_debt_balance: Annotated[Decimal | None, Query(ge=0)] = None,
    max_debt_balance: Annotated[Decimal | None, Query(ge=0)] = None,
    overdue: Annotated[
        bool | None,
        Query(description="Has at least one debt-ledger row flagged overdue"),
    ] = None,
    recently_paid_days: Annotated[
        int | None,
        Query(ge=1, description="Made a payment within the last N days"),
    ] = None,
) -> CustomerListOut:
    return await service.search(
        search=search,
        status=status_filter,
        order=order,
        limit=limit,
        offset=offset,
        credit_level=credit_level,
        is_blocked=is_blocked,
        has_active_debt=has_active_debt,
        min_debt_balance=min_debt_balance,
        max_debt_balance=max_debt_balance,
        overdue=overdue,
        recently_paid_days=recently_paid_days,
    )


# ----- Credit board — static routes registered BEFORE /{customer_id} -------- #
@router.get(
    "/high-risk",
    response_model=CustomerCreditListOut,
    summary="Customers whose credit_level is HIGH_RISK, riskiest first",
)
async def list_high_risk_customers(
    service: CustomerServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerCreditListOut:
    return await service.list_high_risk(limit=limit, offset=offset)


@router.get(
    "/excellent",
    response_model=CustomerCreditListOut,
    summary="Customers whose credit_level is EXCELLENT, best score first",
)
async def list_excellent_customers(
    service: CustomerServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerCreditListOut:
    return await service.list_excellent(limit=limit, offset=offset)


@router.get(
    "/recommendation",
    response_model=CustomerCreditListOut,
    summary="Credit board across all customers — who to extend, who to avoid",
)
async def list_customer_recommendations(
    service: CustomerServiceDep,
    credit_level: Annotated[CreditLevelQuery | None, Query()] = None,
    is_blocked: Annotated[bool | None, Query()] = None,
    has_active_debt: Annotated[bool | None, Query()] = None,
    min_debt_balance: Annotated[Decimal | None, Query(ge=0)] = None,
    max_debt_balance: Annotated[Decimal | None, Query(ge=0)] = None,
    overdue: Annotated[bool | None, Query()] = None,
    recently_paid_days: Annotated[int | None, Query(ge=1)] = None,
    order: Annotated[
        Literal["score_asc", "score_desc", "debt_desc", "name_asc", "recent"],
        Query(),
    ] = "score_asc",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerCreditListOut:
    return await service.list_credit_board(
        credit_level=credit_level,
        is_blocked=is_blocked,
        has_active_debt=has_active_debt,
        min_debt_balance=min_debt_balance,
        max_debt_balance=max_debt_balance,
        overdue=overdue,
        recently_paid_days=recently_paid_days,
        order=order,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/credit-history",
    response_model=CreditEventListOut,
    summary="Decision Engine credit event timeline (DukaMate's credit score history)",
)
async def get_credit_history(
    service: CustomerServiceDep,
    customer_id: Annotated[
        uuid.UUID | None,
        Query(description="Narrow the timeline to a single customer"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CreditEventListOut:
    return await service.get_credit_history(
        customer_id=customer_id, limit=limit, offset=offset
    )


# ----- Single-customer routes ------------------------------------------------ #
@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(
    customer_id: uuid.UUID, service: CustomerServiceDep
) -> CustomerOut:
    return await service.get(customer_id)


@router.get("/{customer_id}/credit", response_model=CustomerCreditOut)
async def get_customer_credit(
    customer_id: uuid.UUID, service: CustomerServiceDep
) -> CustomerCreditOut:
    """Full credit profile: score, level, limit, outstanding balance, payment
    behaviour counters, oldest unpaid debt, reason, and risk indicators."""
    return await service.get_credit(customer_id)


@router.get(
    "/{customer_id}/scores",
    response_model=CustomerScoresOut,
    summary="Reecod Credit Trust + Buyer Value scores for this customer",
)
async def get_customer_scores(
    customer_id: uuid.UUID,
    service: ScoringServiceDep,
    view_mode: Annotated[
        CustomerViewMode,
        Query(description="Which lens is active for the primary UI (spec's Single Lens UI Rule)"),
    ] = "trust",
) -> CustomerScoresOut:
    """Reecod Engine Logic Spec — Customer & Supplier Scoring V1/V2.

    Returns both the Credit Trust and Buyer Value lenses; ``active_view``
    tells the frontend which one to show as the single primary score. Either
    lens may come back with ``status: "insufficient_data"`` (never a
    fabricated percentage) if the minimum clean-record threshold isn't met.
    """
    return await service.get_customer_scores(customer_id, view_mode=view_mode)


@router.get("/{customer_id}/ledger", response_model=CustomerLedgerListOut)
async def get_customer_ledger(
    customer_id: uuid.UUID,
    service: CustomerServiceDep,
    status_filter: Annotated[LedgerStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerLedgerListOut:
    """Per-sale debt ledger: every debt this customer has ever been issued,
    with remaining balance and status, newest first."""
    return await service.get_ledger(
        customer_id, status=status_filter, limit=limit, offset=offset
    )


@router.get("/{customer_id}/payments", response_model=CustomerPaymentHistoryListOut)
async def get_customer_payment_history(
    customer_id: uuid.UUID,
    service: CustomerServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerPaymentHistoryListOut:
    """Payment history for this customer, each payment paired with exactly
    which debts it was allocated to (oldest-first)."""
    return await service.get_payment_history(customer_id, limit=limit, offset=offset)


@router.patch("/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdateRequest,
    service: CustomerServiceDep,
) -> CustomerOut:
    return await service.update(customer_id, payload)


@router.post("/{customer_id}/archive", response_model=CustomerOut)
async def archive_customer(
    customer_id: uuid.UUID, service: CustomerServiceDep
) -> CustomerOut:
    return await service.archive(customer_id)


@router.post("/{customer_id}/simulate-credit", response_model=CreditSimulationOut)
async def simulate_credit(
    customer_id: uuid.UUID,
    payload: CreditSimulationRequest,
    service: CustomerServiceDep,
) -> CreditSimulationOut:
    """"Would giving this customer {requested_amount} on credit be approved
    right now?" — a pure what-if projection; never writes anything."""
    return await service.simulate_credit(customer_id, payload.requested_amount)
