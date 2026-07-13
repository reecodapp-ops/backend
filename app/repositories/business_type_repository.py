"""Read-only repository for the business_types lookup table."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lookups import BusinessType


class BusinessTypeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[BusinessType]:
        stmt = (
            select(BusinessType)
            .where(BusinessType.is_active.is_(True))
            .order_by(BusinessType.sort_order.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())