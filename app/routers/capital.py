"""Capital Transaction router (per blueprint F11). Requires X-Business-Id header.

    POST /capital-transactions               record MONEY_IN / MONEY_OUT / PERSONAL_USE
    GET  /capital-transactions               history (?type=&from=&to=)
    GET  /capital-source-types               seeded list of MONEY_IN source types

OPENING_BALANCE is intentionally NOT acceptable here — it is recorded only at
business onboarding via the businesses.opening_balance column.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.master_data_deps import CapitalServiceDep
from app.schemas.capital import (
    CapitalSourceTypeOut,
    CapitalTransactionCreateRequest,
    CapitalTransactionCreateResponse,
    CapitalTransactionListOut,
    CapitalTransactionOut,
    CapitalTypeFilter,
)

router = APIRouter(prefix="/capital-transactions", tags=["Capital"])
sources_router = APIRouter(prefix="/capital-source-types", tags=["Capital"])


@router.post(
    "",
    response_model=CapitalTransactionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_capital_transaction(
    payload: CapitalTransactionCreateRequest, service: CapitalServiceDep
) -> CapitalTransactionCreateResponse:
    return await service.create_capital_transaction(payload)


@router.get("", response_model=CapitalTransactionListOut)
async def list_capital_transactions(
    service: CapitalServiceDep,
    type_code: Annotated[
        CapitalTypeFilter | None,
        Query(
            alias="type",
            description=(
                "MONEY_IN / MONEY_OUT / PERSONAL_USE / OPENING_BALANCE. "
                "OPENING_BALANCE is a read-only synthetic entry derived from "
                "the business's opening_balance — see CapitalService."
            ),
        ),
    ] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CapitalTransactionListOut:
    return await service.list_capital_transactions(
        type_code=type_code,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/{txn_id}", response_model=CapitalTransactionOut)
async def get_capital_transaction(
    txn_id: uuid.UUID, service: CapitalServiceDep
) -> CapitalTransactionOut:
    return await service.get_capital_transaction(txn_id)


@sources_router.get("", response_model=list[CapitalSourceTypeOut])
async def list_capital_sources(
    service: CapitalServiceDep,
) -> list[CapitalSourceTypeOut]:
    return await service.list_source_types()
