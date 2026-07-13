"""Verification code repository.

Encapsulates: issuing OTPs, looking up the active code for verification,
marking codes as used, computing the cooldown for rate-limited resends, and
cleaning up expired/used codes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.verification_code import VerificationCode


class VerificationCodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----- create ---------------------------------------------------------- #
    async def insert(
        self,
        *,
        user_id: uuid.UUID,
        code: str,
        verification_type: str,
        expires_at: datetime,
    ) -> VerificationCode:
        # Set created_at explicitly: server_default=NOW() on SQLite resolves
        # to CURRENT_TIMESTAMP which has 1-second resolution. Two OTPs minted
        # in the same second tie on created_at, making "latest" ambiguous.
        # Setting it from Python preserves microsecond precision so the
        # ORDER BY created_at DESC in get_latest_for is deterministic.
        vc = VerificationCode(
            user_id=user_id,
            code=code,
            verification_type=verification_type,
            expires_at=expires_at,
            used_at=None,
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(vc)
        await self._session.flush()
        return vc

    # ----- read ------------------------------------------------------------ #
    async def get_latest_for(
        self, user_id: uuid.UUID, verification_type: str
    ) -> VerificationCode | None:
        """Most recent issuance for (user, type), regardless of status. Used
        for resend cooldown and verify lookups.

        Ordering tiebreak on id: SQLite's CURRENT_TIMESTAMP has 1-second
        resolution, so two codes minted in the same second can tie on
        created_at and the result becomes non-deterministic. Adding id DESC
        as a secondary key gives a stable ordering for tests; in production
        with PostgreSQL's NOW() (microsecond resolution) the tie is unlikely
        but the secondary key is still defensive.
        """
        result = await self._session.execute(
            select(VerificationCode)
            .where(
                VerificationCode.user_id == user_id,
                VerificationCode.verification_type == verification_type,
            )
            .order_by(
                VerificationCode.created_at.desc(),
                VerificationCode.id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_active_match(
        self,
        *,
        user_id: uuid.UUID,
        verification_type: str,
        code: str,
        now: datetime | None = None,
    ) -> VerificationCode | None:
        """Active = unused AND not expired AND code matches.

        The query is parameterised so SQLAlchemy escapes the code value; the
        comparison itself is constant-time-ish at the DB layer (still subject
        to timing channels in the wider request, but the OTP is short-lived
        and rate-limited which mitigates brute force)."""
        moment = now if now is not None else datetime.now(timezone.utc)
        result = await self._session.execute(
            select(VerificationCode).where(
                VerificationCode.user_id == user_id,
                VerificationCode.verification_type == verification_type,
                VerificationCode.code == code,
                VerificationCode.used_at.is_(None),
                VerificationCode.expires_at > moment,
            )
        )
        return result.scalar_one_or_none()

    # ----- update ---------------------------------------------------------- #
    async def mark_used(self, vc: VerificationCode) -> None:
        vc.used_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def invalidate_active(
        self, *, user_id: uuid.UUID, verification_type: str
    ) -> None:
        """Mark any still-active codes for (user, type) as used so resending an
        OTP doesn't leave multiple parallel valid codes around."""
        now = datetime.now(timezone.utc)
        result = await self._session.execute(
            select(VerificationCode).where(
                VerificationCode.user_id == user_id,
                VerificationCode.verification_type == verification_type,
                VerificationCode.used_at.is_(None),
                VerificationCode.expires_at > now,
            )
        )
        for vc in result.scalars().all():
            vc.used_at = now
        await self._session.flush()

    # ----- cleanup --------------------------------------------------------- #
    async def delete_expired_or_used_older_than(self, cutoff: datetime) -> int:
        """Hard-delete codes that are either used or expired and older than the
        cutoff. Used by the cleanup worker. Returns the number of deleted rows.
        """
        result = await self._session.execute(
            delete(VerificationCode).where(
                and_(
                    VerificationCode.created_at < cutoff,
                    or_(
                        VerificationCode.used_at.is_not(None),
                        VerificationCode.expires_at <= cutoff,
                    ),
                )
            )
        )
        await self._session.flush()
        return result.rowcount or 0


__all__ = ["VerificationCodeRepository"]
