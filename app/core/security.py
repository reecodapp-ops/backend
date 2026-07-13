"""Security primitives: password hashing and JWT encode/decode.

- Passwords are hashed with bcrypt directly (the ``bcrypt`` package). bcrypt
  operates on at most 72 bytes; password length is bounded at the schema layer
  (max 128 chars) and bcrypt's own limit is respected explicitly.
- JWTs use HS256. Access tokens are short-lived; refresh tokens are long-lived
  and carry a ``type`` claim so a refresh token cannot be used as an access token.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

# bcrypt only considers the first 72 bytes of the input. We encode to UTF-8 and
# truncate to 72 bytes so longer passwords hash deterministically rather than
# raising. Combined with the schema's max_length=128, this is safe and explicit.
_BCRYPT_MAX_BYTES = 72


def _to_bcrypt_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #
def hash_password(plain_password: str) -> str:
    """Return a bcrypt hash (utf-8 string) of the given plaintext password."""
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    digest = bcrypt.hashpw(_to_bcrypt_bytes(plain_password), salt)
    return digest.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(
            _to_bcrypt_bytes(plain_password), password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def _create_token(
    *,
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def create_access_token(
    subject: str, extra_claims: dict[str, Any] | None = None
) -> str:
    """Create a short-lived access token for ``subject`` (user id as string)."""
    return _create_token(
        subject=subject,
        token_type=ACCESS_TOKEN_TYPE,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        extra_claims=extra_claims,
    )


def create_refresh_token(subject: str) -> str:
    """Create a long-lived refresh token for ``subject``."""
    return _create_token(
        subject=subject,
        token_type=REFRESH_TOKEN_TYPE,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT, returning its claims.

    Raises ``JWTError`` (or subclass) on invalid signature/expiry, which callers
    translate into HTTP 401.
    """
    return jwt.decode(
        token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )


__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "JWTError",
    "ACCESS_TOKEN_TYPE",
    "REFRESH_TOKEN_TYPE",
]
