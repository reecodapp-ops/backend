"""FastAPI dependency providers.

Wires the request-scoped database session into repositories/services and
provides ``get_current_user`` which validates the bearer access token.
Business-scoped routes will layer an additional dependency on top of this one
that resolves and validates ``business_id`` and sets the RLS session variable.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.core.security import ACCESS_TOKEN_TYPE, JWTError, decode_token
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.services.business_service import BusinessService
from app.services.insights_service import InsightsService

# auto_error=False so we can raise our own uniform 401 envelope.
_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_auth_service(session: DbSession) -> AuthService:
    return AuthService(session)


def get_business_service(session: DbSession) -> BusinessService:
    return BusinessService(session)


def get_insights_service(session: DbSession) -> InsightsService:
    return InsightsService(session)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
BusinessServiceDep = Annotated[BusinessService, Depends(get_business_service)]
InsightsServiceDep = Annotated[InsightsService, Depends(get_insights_service)]


async def get_current_user(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the authenticated user from a bearer access token."""
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


CurrentUser = Annotated[User, Depends(get_current_user)]
