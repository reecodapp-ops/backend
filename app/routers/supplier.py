"""Supplier router (per blueprint F4). All routes require X-Business-Id header.

    POST   /suppliers              create
    GET    /suppliers              search/list (?search=&status=&order=&limit=&offset=)
    GET    /suppliers/{id}         detail
    PATCH  /suppliers/{id}         update (incl. status=ARCHIVED)
    POST   /suppliers/{id}/archive archive (soft delete)
"""
from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.core.master_data_deps import ScoringServiceDep, SupplierServiceDep
from app.schemas.supplier import (
    ReliabilityLevel,
    SupplierCreateRequest,
    SupplierListOut,
    SupplierOrder,
    SupplierOut,
    SupplierUpdateRequest,
)
from app.schemas.scoring import SupplierScoresOut, SupplierViewMode

router = APIRouter(prefix="/suppliers", tags=["Suppliers"])


@router.post("", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    payload: SupplierCreateRequest, service: SupplierServiceDep
) -> SupplierOut:
    return await service.create(payload)


@router.get("", response_model=SupplierListOut)
async def search_suppliers(
    service: SupplierServiceDep,
    search: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[
        Literal["ACTIVE", "ARCHIVED"] | None, Query(alias="status")
    ] = "ACTIVE",
    reliability_level: Annotated[
        ReliabilityLevel | None,
        Query(
            description=(
                "Filter by Reecod Reliability Score tier (spec §2.3). "
                "Computed on read, same as GET /suppliers/{id}/scores."
            )
        ),
    ] = None,
    order: Annotated[SupplierOrder, Query()] = "name_asc",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SupplierListOut:
    return await service.search(
        search=search,
        status=status_filter,
        reliability_level=reliability_level,
        order=order,
        limit=limit,
        offset=offset,
    )


@router.get("/{supplier_id}", response_model=SupplierOut)
async def get_supplier(
    supplier_id: uuid.UUID, service: SupplierServiceDep
) -> SupplierOut:
    return await service.get(supplier_id)


@router.patch("/{supplier_id}", response_model=SupplierOut)
async def update_supplier(
    supplier_id: uuid.UUID,
    payload: SupplierUpdateRequest,
    service: SupplierServiceDep,
) -> SupplierOut:
    return await service.update(supplier_id, payload)


@router.post("/{supplier_id}/archive", response_model=SupplierOut)
async def archive_supplier(
    supplier_id: uuid.UUID, service: SupplierServiceDep
) -> SupplierOut:
    return await service.archive(supplier_id)


@router.get(
    "/{supplier_id}/scores",
    response_model=SupplierScoresOut,
    summary="Reecod Reliability + Terms scores for this supplier",
)
async def get_supplier_scores(
    supplier_id: uuid.UUID,
    service: ScoringServiceDep,
    view_mode: Annotated[
        SupplierViewMode,
        Query(description="Which lens is active for the primary UI (spec's Single Lens UI Rule)"),
    ] = "reliability",
) -> SupplierScoresOut:
    """Reecod Engine Logic Spec — Customer & Supplier Scoring V1/V2.

    Returns both the Reliability and Terms lenses; ``active_view`` tells the
    frontend which one to show as the single primary score. Either lens may
    come back with ``status: "insufficient_data"`` if the minimum
    clean-record threshold isn't met.
    """
    return await service.get_supplier_scores(supplier_id, view_mode=view_mode)
