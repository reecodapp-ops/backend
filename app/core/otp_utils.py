"""OTP generation utilities.

Uses ``secrets`` rather than ``random``: ``secrets.randbelow`` is the right
primitive for any token a user types back. The codes are zero-padded to the
configured length so leading zeros are preserved.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from app.core.config import settings


def generate_otp(length: int | None = None) -> str:
    """Return a fresh zero-padded numeric OTP of the configured length.

    ``secrets.randbelow(10**length)`` distributes uniformly across the full
    range including codes with leading zeros (e.g. '004217').
    """
    n = length if length is not None else settings.otp_length
    if n < 4 or n > 10:
        raise ValueError("OTP length must be between 4 and 10")
    upper = 10 ** n
    code = secrets.randbelow(upper)
    return str(code).zfill(n)


def otp_expires_at(now: datetime | None = None) -> datetime:
    """Compute the expiry timestamp for an OTP issued at ``now``."""
    moment = now if now is not None else datetime.now(timezone.utc)
    return moment + timedelta(minutes=settings.otp_ttl_minutes)


def is_expired(expires_at: datetime, now: datetime | None = None) -> bool:
    """True if ``expires_at`` has already passed."""
    moment = now if now is not None else datetime.now(timezone.utc)
    # Comparison is tz-aware on both sides; rows from the DB are TIMESTAMPTZ.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return moment >= expires_at


__all__ = ["generate_otp", "otp_expires_at", "is_expired"]
