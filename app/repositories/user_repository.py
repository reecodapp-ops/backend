"""User repository — email-based lookups."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


def _normalize_email(email: str) -> str:
    """Normalize an email for storage/lookup: lowercase, stripped."""
    return email.strip().lower()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(func.lower(User.email) == _normalize_email(email))
        )
        return result.scalar_one_or_none()

    async def exists_by_email(self, email: str) -> bool:
        result = await self._session.execute(
            select(User.id).where(func.lower(User.email) == _normalize_email(email))
        )
        return result.scalar_one_or_none() is not None

    async def create_unverified(
        self, *, email: str, full_name: str
    ) -> User:
        user = User(
            email=_normalize_email(email),
            full_name=full_name,
            password_hash=None,
            is_email_verified=False,
            is_phone_verified=False,
            is_active=True,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def mark_email_verified(self, user: User) -> None:
        user.is_email_verified = True
        user.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def set_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        user.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def touch_last_login(self, user: User) -> None:
        user.last_login_at = datetime.now(timezone.utc)
        await self._session.flush()
