"""Business-scoping dependency for multi-tenant routes.

Master-data and transaction routes operate within a single business. The active
business is provided by the client via the ``X-Business-Id`` header (kept out of
request bodies and paths per the blueprint convention that ``business_id`` is
never client-supplied in write payloads).

This dependency:
  1. reads and validates the ``X-Business-Id`` header,
  2. verifies the authenticated user owns that business,
  3. sets the PostgreSQL RLS session variable ``app.current_business_id`` so the
     database enforces tenant isolation as a hard fail-safe.

On non-PostgreSQL backends (e.g. SQLite in tests) the ``SET LOCAL`` call is
skipped gracefully; application-layer ownership checks still apply.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.security import ACCESS_TOKEN_TYPE, JWTError, decode_token
from app.models.business import Business
from app.models.user import User
from app.repositories.business_repository import BusinessRepository
from app.repositories.user_repository import UserRepository
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


async def _resolve_current_user(
    session: AsyncSession,
    credentials: HTTPAuthorizationCredentials | None,
) -> User:
    """Resolve the authenticated user from a bearer access token.

    Defined here (rather than imported from app.core.dependencies) to avoid a
    circular import: dependencies -> services -> business_context -> dependencies.
    """
    from app.core.exceptions import UnauthorizedError

    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Missing authentication token.", code="missing_token")
    try:
        claims = decode_token(credentials.credentials)
    except JWTError as exc:
        raise UnauthorizedError(
            "Invalid or expired token.", code="invalid_token"
        ) from exc
    if claims.get("type") != ACCESS_TOKEN_TYPE:
        raise UnauthorizedError(
            "An access token is required.", code="wrong_token_type"
        )
    subject = claims.get("sub")
    if not subject:
        raise UnauthorizedError("Malformed token.", code="invalid_token")
    user = await UserRepository(session).get_by_id(uuid.UUID(subject))
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive.", code="invalid_token")
    return user


class BusinessContext:
    """Resolved tenant context passed to business-scoped services."""

    def __init__(self, business: Business, session: AsyncSession) -> None:
        self.business = business
        self.business_id = business.id
        self.session = session


async def get_business_context(
    session: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
    x_business_id: Annotated[str | None, Header(alias="X-Business-Id")] = None,
) -> BusinessContext:
    current_user = await _resolve_current_user(session, credentials)

    if not x_business_id:
        raise ValidationError(
            "Missing X-Business-Id header.", code="missing_business_id"
        )
    try:
        business_id = uuid.UUID(x_business_id)
    except (ValueError, TypeError) as exc:
        raise ValidationError(
            "X-Business-Id is not a valid UUID.", code="invalid_business_id"
        ) from exc

    repo = BusinessRepository(session)
    business = await repo.get_by_id(business_id)
    if business is None:
        raise NotFoundError("Business not found.", code="business_not_found")
    if business.owner_id != current_user.id:
        raise ForbiddenError(
            "You do not have access to this business.",
            code="business_not_owned",
        )

    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.current_business_id', :bid, true)"),
            {"bid": str(business_id)},
        )

    return BusinessContext(business=business, session=session)


BusinessCtx = Annotated[BusinessContext, Depends(get_business_context)]
