"""Shared pytest fixtures for the DukaMate test suite.

Uses an in-memory SQLite database (StaticPool, so every connection in a test
sees the same DB) with all ORM tables created fresh per test. This exercises
the full service/repository/schema stack exactly as production does, with one
documented exception: PostgreSQL-only features (triggers, functions, RLS,
views such as v_daily_summary/v_customer_credit) do not exist on SQLite. Tests
that need trigger-computed aggregates (customer.debt_balance,
customer_debt_ledger rows, credit score fields) seed that state directly via
the ORM instead of relying on a trigger to produce it — this is the same
pattern used throughout the project's own smoke-testing during development.

Fixtures:
    engine, session          — raw async SQLAlchemy plumbing
    seed_lookups              — inserts the lookup/reference rows every module
                                 needs (business types, currencies, payment
                                 types, capital transaction types, issue
                                 types, expense categories)
    business_ctx               — a fully onboarded Business + BusinessContext,
                                 ready to hand to any *Service class directly
    client                     — an httpx.AsyncClient wired to the real FastAPI
                                 app via ASGITransport, with the DB dependency
                                 overridden to use the test session and a
                                 valid bearer token already attached
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import create_access_token
from app.models.base import Base
import app.models  # noqa: F401 — populate Base.metadata
from app.models.business import CapitalTransaction
from app.models.expense import Expense, ExpenseCategory, ExpenseCategoryGroup
from app.models.issue import IssueType
from app.models.lookups import (
    BusinessType,
    CapitalSourceType,
    CapitalTransactionType,
    Currency,
    PaymentType,
    SubscriptionPlan,
)
from app.models.user import User
from app.core.business_context import BusinessContext
from app.repositories.business_repository import BusinessRepository
from app.schemas.business import BusinessCreateRequest
from app.services.business_service import BusinessService


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def sessionmaker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(sessionmaker) -> AsyncSession:
    async with sessionmaker() as s:
        yield s


# Issue type codes seeded by the schema (dukamate_schema_v3.sql section 1.7).
_ISSUE_TYPES = [
    ("LOW_MARGIN", "Item moving but leaving little money", 1, "WARN"),
    ("STOCK_FINISHING", "This item may finish soon", 1, "WARN"),
    ("DEBT_OVERDUE", "Customer debt overdue", 1, "WARN"),
    ("EXPENSE_PRESSURE", "Expenses eating too much of sales", 2, "WARN"),
    ("CUSTOMER_HIGH_RISK", "Customer credit risk is high", 1, "WARN"),
    ("CUSTOMER_EXCELLENT", "Customer is a trusted payer", 2, "INFO"),
    ("CREDIT_LIMIT_REACHED", "Customer reached their recommended limit", 1, "WARN"),
    ("PAYMENT_OVERDUE", "Customer payment is overdue", 1, "WARN"),
    ("REPEATED_DEFAULT", "Customer has a pattern of late payments", 2, "ALERT"),
]


@pytest_asyncio.fixture
async def seed_lookups(session: AsyncSession) -> None:
    """Insert every reference/lookup row the application assumes is seeded
    (mirrors the INSERT statements in dukamate_schema_v3.sql)."""
    session.add(BusinessType(id=1, code="RETAIL_SHOP", label="Retail shop", sort_order=1))
    session.add(Currency(id=1, iso_code="UGX", name="Ugandan Shilling", symbol="USh", decimal_places=0))
    session.add(Currency(id=2, iso_code="USD", name="US Dollar", symbol="$", decimal_places=2))
    session.add(SubscriptionPlan(id=1, code="FREE", label="Free", engine_phase_max=1, price_monthly=Decimal("0")))
    session.add(SubscriptionPlan(id=2, code="STARTER", label="Starter", engine_phase_max=2, price_monthly=Decimal("5")))
    session.add(PaymentType(id=1, code="CASH", label="Paid now", sort_order=1))
    session.add(PaymentType(id=2, code="ON_DEBT", label="On credit", sort_order=2))
    session.add(CapitalTransactionType(id=1, code="OPENING_BALANCE", label="Opening balance", direction="I", sort_order=1))
    session.add(CapitalTransactionType(id=2, code="MONEY_IN", label="Money added", direction="I", sort_order=2))
    session.add(CapitalTransactionType(id=3, code="MONEY_OUT", label="Money removed", direction="O", sort_order=3))
    session.add(CapitalTransactionType(id=4, code="PERSONAL_USE", label="Personal use", direction="O", sort_order=4))
    session.add(CapitalSourceType(id=1, code="PERSONAL_SAVINGS", label="Personal savings", sort_order=1))
    session.add(ExpenseCategoryGroup(id=1, code="BUSINESS", label="Business expense", sort_order=1))
    session.add(ExpenseCategoryGroup(id=2, code="PERSONAL", label="Personal use", sort_order=2))
    session.add(
        ExpenseCategory(id=1, group_id=1, code="RENT", label="Rent", sort_order=1, is_system=True)
    )
    session.add(
        ExpenseCategory(id=2, group_id=2, code="HOME", label="Home", sort_order=2, is_system=True)
    )
    for code, label, phase, sev in _ISSUE_TYPES:
        session.add(
            IssueType(code=code, label=label, engine_phase=phase, severity_default=sev)
        )
    await session.flush()
    await session.commit()


@pytest_asyncio.fixture
async def owner(session: AsyncSession, seed_lookups) -> User:
    user = User(
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        full_name="Test Owner",
        is_email_verified=True,
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def business_ctx(session: AsyncSession, owner: User) -> BusinessContext:
    """A fully onboarded business (FREE plan, UGX currency), ready to hand to
    any Service class directly (no HTTP involved)."""
    service = BusinessService(session)
    detail = await service.create(
        owner,
        BusinessCreateRequest(
            shop_name="Test Shop",
            business_type_id=1,
            currency_id=1,
            phone_number="+256700000000",
            location="Kampala Road",
            opening_balance=Decimal("0"),
        ),
    )
    await session.commit()
    business = await BusinessRepository(session).get_by_id(detail.id)
    return BusinessContext(business=business, session=session)


@pytest_asyncio.fixture
async def client(sessionmaker, owner: User):
    """httpx.AsyncClient wired to the real FastAPI app, DB dependency
    overridden to the test sessionmaker, with a valid bearer token for
    ``owner`` already attached."""
    from app.main import app as fastapi_app

    async def _override_get_db():
        async with sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    token = create_access_token(str(owner.id))
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()
