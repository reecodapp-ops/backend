"""Correction router (per blueprint F12). Requires X-Business-Id header.

    POST /corrections     create a correction (flips original, inserts replacement,
                          writes audit row, reconciles cash + debt)
    GET  /corrections     list the audit log

Supported correction_type_codes for V1: SALE_EDIT, EXPENSE_EDIT, PAYMENT_EDIT,
CAPITAL_EDIT. Others return 422 (unsupported_correction_type) — see correction
service docstring for the rationale.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.master_data_deps import CorrectionServiceDep
from app.schemas.correction import (
    CorrectionCreateRequest,
    CorrectionCreateResponse,
    CorrectionListOut,
)

router = APIRouter(prefix="/corrections", tags=["Corrections"])


@router.post(
    "",
    response_model=CorrectionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_correction(
    payload: CorrectionCreateRequest, service: CorrectionServiceDep
) -> CorrectionCreateResponse:
    return await service.create_correction(payload)


@router.get("", response_model=CorrectionListOut)
async def list_corrections(
    service: CorrectionServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CorrectionListOut:
    return await service.list_corrections(limit=limit, offset=offset)
