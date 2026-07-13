"""Issue router (per blueprint F15 + Decision Engine). Requires X-Business-Id header.

    GET    /issues                  list (filterable, paginated)
    PATCH  /issues/{id}             { status: RESOLVED | IGNORED }
    POST   /issues/recompute        run the Decision Engine for this business
"""
from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.core.master_data_deps import IssueServiceDep
from app.schemas.issue import (
    IssueListOut,
    IssueOut,
    IssueUpdateRequest,
    RecomputeResponse,
)

router = APIRouter(prefix="/issues", tags=["Issues"])


@router.get("", response_model=IssueListOut)
async def list_issues(
    service: IssueServiceDep,
    status_filter: Annotated[
        Literal["ACTIVE", "RESOLVED", "IGNORED", "SUPERSEDED"] | None,
        Query(alias="status"),
    ] = "ACTIVE",
    min_confidence: Annotated[int | None, Query(ge=1, le=4)] = None,
    severity: Annotated[
        Literal["INFO", "WARN", "ALERT"] | None, Query()
    ] = None,
    customer_id: Annotated[
        uuid.UUID | None, Query(description="Issues referencing this customer")
    ] = None,
    product_id: Annotated[
        uuid.UUID | None, Query(description="Issues referencing this product")
    ] = None,
    resolved: Annotated[
        bool | None,
        Query(description="Convenience alias for status=RESOLVED / status!=RESOLVED"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IssueListOut:
    return await service.list_issues(
        status=status_filter,
        min_confidence=min_confidence,
        severity=severity,
        ref_customer_id=customer_id,
        ref_product_id=product_id,
        resolved=resolved,
        limit=limit,
        offset=offset,
    )


@router.patch("/{issue_id}", response_model=IssueOut)
async def update_issue_status(
    issue_id: uuid.UUID,
    payload: IssueUpdateRequest,
    service: IssueServiceDep,
) -> IssueOut:
    return await service.update_status(issue_id, payload)


@router.post(
    "/recompute",
    response_model=RecomputeResponse,
    status_code=status.HTTP_200_OK,
    summary="Run the Decision Engine for the current business",
)
async def recompute_issues(service: IssueServiceDep) -> RecomputeResponse:
    return await service.recompute()
