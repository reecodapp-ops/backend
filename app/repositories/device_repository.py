"""Device repository — all database access for the ``devices`` table.

Handles the shared-device lifecycle: register/refresh a device on login,
and deactivate it on logout.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, device_id: uuid.UUID) -> Device | None:
        result = await self._session.execute(
            select(Device).where(Device.id == device_id)
        )
        return result.scalar_one_or_none()

    async def get_for_user_by_fingerprint(
        self, user_id: uuid.UUID, fingerprint: str
    ) -> Device | None:
        result = await self._session.execute(
            select(Device).where(
                Device.user_id == user_id,
                Device.device_fingerprint == fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_on_login(
        self,
        *,
        user_id: uuid.UUID,
        fingerprint: str,
        platform: str,
        app_version: str | None,
    ) -> Device:
        """Reactivate an existing device for this user/fingerprint, or create one."""
        device = await self.get_for_user_by_fingerprint(user_id, fingerprint)
        now = datetime.now(timezone.utc)
        if device is not None:
            device.platform = platform
            device.app_version = app_version
            device.last_seen_at = now
            device.is_active = True
        else:
            device = Device(
                user_id=user_id,
                device_fingerprint=fingerprint,
                platform=platform,
                app_version=app_version,
                last_seen_at=now,
                is_active=True,
            )
            self._session.add(device)
        await self._session.flush()
        await self._session.refresh(device)
        return device

    async def deactivate(self, device: Device) -> None:
        device.is_active = False
        await self._session.flush()
