"""Async SQLAlchemy 2.0 engine and session management.

The engine and sessionmaker are created lazily so that importing the
application does not require the database driver to be installed/connectable
at import time (important for tests that swap in a different database, and for
tooling that imports the app without a live DB).

Exposes:
- ``get_engine`` / ``get_sessionmaker``: lazy accessors
- ``get_db``: FastAPI dependency yielding a session with commit/rollback handling
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async sessionmaker, creating it on first use."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session, committing on success and rolling back on error.

    Used as a FastAPI dependency. Each request gets its own session; the
    multi-tenant RLS context (``app.current_business_id``) is set by a separate
    dependency layered on top of this one for business-scoped routes.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
