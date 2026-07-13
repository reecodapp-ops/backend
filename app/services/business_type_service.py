"""Service layer for business type lookups."""
from __future__ import annotations

from app.repositories.business_type_repository import BusinessTypeRepository
from app.schemas.business_type import BusinessTypeListResponse, BusinessTypeOut


class BusinessTypeService:
    def __init__(self, repo: BusinessTypeRepository) -> None:
        self._repo = repo

    async def list_business_types(self) -> BusinessTypeListResponse:
        rows = await self._repo.list_active()
        return BusinessTypeListResponse(
            business_types=[BusinessTypeOut.model_validate(r) for r in rows]
        )