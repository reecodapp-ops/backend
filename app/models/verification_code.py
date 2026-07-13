"""Verification code model — OTPs for email verification, password reset, and
email change.

Schema:
    id                 UUID PK
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
    code               VARCHAR(10) NOT NULL   (currently always 6 digits)
    verification_type  VARCHAR(20) NOT NULL CHECK IN (
                          'EMAIL_VERIFICATION','PASSWORD_RESET','EMAIL_CHANGE')
    expires_at         TIMESTAMPTZ NOT NULL
    used_at            TIMESTAMPTZ NULL
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()

The same row records issue time (created_at), use time (used_at), and
expiration (expires_at) so:
  * the OTP can only be redeemed once (used_at IS NULL guard);
  * verification respects the configured TTL (expires_at > now);
  * resend rate limiting is computed off the most recent created_at for the
    same (user, type).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column, uuid_pk


VERIFICATION_TYPE_EMAIL = "EMAIL_VERIFICATION"
VERIFICATION_TYPE_PASSWORD_RESET = "PASSWORD_RESET"
VERIFICATION_TYPE_EMAIL_CHANGE = "EMAIL_CHANGE"

VERIFICATION_TYPES = (
    VERIFICATION_TYPE_EMAIL,
    VERIFICATION_TYPE_PASSWORD_RESET,
    VERIFICATION_TYPE_EMAIL_CHANGE,
)


class VerificationCode(Base):
    __tablename__ = "verification_codes"
    __table_args__ = (
        CheckConstraint(
            "verification_type IN ('EMAIL_VERIFICATION','PASSWORD_RESET','EMAIL_CHANGE')",
            name="verification_codes_type_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    verification_type: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="verification_codes")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<VerificationCode user={self.user_id} "
            f"type={self.verification_type} used={self.used_at is not None}>"
        )
