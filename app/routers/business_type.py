"""Business types router — global lookup table, no X-Business-Id required.

    GET /business-types    list active business types, sorted for display
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.master_data_deps import BusinessTypeServiceDep
from app.schemas.business_type import BusinessTypeListResponse

router = APIRouter(prefix="/business-types", tags=["Business Types"])


@router.get(
    "",
    response_model=BusinessTypeListResponse,
    summary="List active business types (for onboarding / settings dropdowns)",
)
async def list_business_types(
    service: BusinessTypeServiceDep,
) -> BusinessTypeListResponse:
    return await service.list_business_types()