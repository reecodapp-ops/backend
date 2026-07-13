"""Customer repository — all DB access for the ``customers`` table.

Search uses ILIKE on full_name; on PostgreSQL the pg_trgm GIN index
(idx_customers_name_trgm) accelerates ``ILIKE '%term%'`` substring matching.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.payment import CustomerPayment
from app.models.sale import CustomerDebtLedger, Sale


class CustomerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _biz_param(self, business_id: uuid.UUID) -> str:
        """Format a UUID for a text() bind matching how the dialect stores it
        (see DashboardRepository for the same helper/rationale)."""
        bind = self._session.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            return business_id.hex
        return str(business_id)

    async def get_by_id(
        self, business_id: uuid.UUID, customer_id: uuid.UUID
    ) -> Customer | None:
        result = await self._session.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.business_id == business_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_name(
        self, business_id: uuid.UUID, full_name: str
    ) -> Customer | None:
        result = await self._session.execute(
            select(Customer).where(
                Customer.business_id == business_id,
                func.lower(Customer.full_name) == full_name.lower(),
            )
        )
        return result.scalars().first()

    async def create(
        self,
        *,
        business_id: uuid.UUID,
        full_name: str,
        phone_number: str | None,
        notes: str | None,
    ) -> Customer:
        customer = Customer(
            business_id=business_id,
            full_name=full_name,
            phone_number=phone_number,
            notes=notes,
            status="ACTIVE",
        )
        self._session.add(customer)
        await self._session.flush()
        await self._session.refresh(customer)
        return customer

    # ----- filters shared by /customers, /customers/high-risk, ---------------
    # /customers/excellent, /customers/recommendation --------------------------
    def _apply_credit_filters(
        self,
        conditions: list,
        *,
        credit_level: str | None,
        is_blocked: bool | None,
        has_active_debt: bool | None,
        min_debt_balance: Decimal | None,
        max_debt_balance: Decimal | None,
        overdue: bool | None,
        recently_paid_days: int | None,
    ) -> None:
        if credit_level is not None:
            conditions.append(Customer.credit_level == credit_level)
        if is_blocked is not None:
            conditions.append(Customer.is_credit_blocked.is_(is_blocked))
        if has_active_debt is True:
            conditions.append(Customer.debt_balance > 0)
        elif has_active_debt is False:
            conditions.append(Customer.debt_balance <= 0)
        if min_debt_balance is not None:
            conditions.append(Customer.debt_balance >= min_debt_balance)
        if max_debt_balance is not None:
            conditions.append(Customer.debt_balance <= max_debt_balance)
        if overdue is True:
            conditions.append(
                exists().where(
                    CustomerDebtLedger.customer_id == Customer.id,
                    CustomerDebtLedger.risk_flag.is_(True),
                    CustomerDebtLedger.status.in_(["UNPAID", "PARTIAL"]),
                )
            )
        elif overdue is False:
            conditions.append(
                ~exists().where(
                    CustomerDebtLedger.customer_id == Customer.id,
                    CustomerDebtLedger.risk_flag.is_(True),
                    CustomerDebtLedger.status.in_(["UNPAID", "PARTIAL"]),
                )
            )
        if recently_paid_days is not None:
            since = datetime.now(timezone.utc) - timedelta(days=recently_paid_days)
            conditions.append(
                exists().where(
                    CustomerPayment.customer_id == Customer.id,
                    CustomerPayment.is_correction.is_(False),
                    CustomerPayment.occurred_at >= since,
                )
            )

    async def search(
        self,
        *,
        business_id: uuid.UUID,
        search: str | None,
        status: str | None,
        order: str,
        limit: int,
        offset: int,
        credit_level: str | None = None,
        is_blocked: bool | None = None,
        has_active_debt: bool | None = None,
        min_debt_balance: Decimal | None = None,
        max_debt_balance: Decimal | None = None,
        overdue: bool | None = None,
        recently_paid_days: int | None = None,
    ) -> tuple[list[Customer], int]:
        conditions = [Customer.business_id == business_id]
        if status:
            conditions.append(Customer.status == status)
        if search:
            conditions.append(Customer.full_name.ilike(f"%{search}%"))
        self._apply_credit_filters(
            conditions,
            credit_level=credit_level,
            is_blocked=is_blocked,
            has_active_debt=has_active_debt,
            min_debt_balance=min_debt_balance,
            max_debt_balance=max_debt_balance,
            overdue=overdue,
            recently_paid_days=recently_paid_days,
        )

        count_stmt = select(func.count()).select_from(Customer).where(*conditions)
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = select(Customer).where(*conditions)
        if order == "debt_desc":
            stmt = stmt.order_by(Customer.debt_balance.desc())
        elif order == "name_asc":
            stmt = stmt.order_by(Customer.full_name.asc())
        elif order == "score_asc":
            stmt = stmt.order_by(Customer.credit_score.asc().nulls_first())
        elif order == "score_desc":
            stmt = stmt.order_by(Customer.credit_score.desc().nulls_last())
        else:  # recent
            stmt = stmt.order_by(Customer.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)

        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows), total

    # ----- Customer Credibility Engine [NEW v3] ------------------------------ #
    async def get_credit(
        self, business_id: uuid.UUID, customer_id: uuid.UUID
    ) -> dict | None:
        """Read a single customer's row from ``v_customer_credit``.

        Kept for parity/consistency-checking against the DB view, but the
        customer-credit service methods build their response directly from
        the ``Customer`` row instead (identical numbers, and it also lets
        ``reason`` / ``risk_indicators`` be computed the same way on every
        backend, including SQLite in tests where this view doesn't exist).
        """
        bind = self._session.get_bind()
        if bind is not None and bind.dialect.name != "postgresql":
            return None
        result = await self._session.execute(
            text(
                "SELECT * FROM v_customer_credit "
                "WHERE business_id = :biz AND customer_id = :cust"
            ),
            {"biz": self._biz_param(business_id), "cust": self._biz_param(customer_id)},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    # ----- debt ledger (GET /customers/{id}/ledger) --------------------------- #
    async def get_ledger(
        self,
        *,
        business_id: uuid.UUID,
        customer_id: uuid.UUID,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[CustomerDebtLedger, str | None]], int]:
        """Returns (ledger_row, receipt_number) pairs -- receipt_number comes
        from sales.receipt_number via the row's own sale_id, joined into
        this same query (no extra round trip beyond the existing payments
        bulk-fetch in the service layer)."""
        conditions = [
            CustomerDebtLedger.business_id == business_id,
            CustomerDebtLedger.customer_id == customer_id,
        ]
        if status is not None:
            conditions.append(CustomerDebtLedger.status == status)

        count_stmt = (
            select(func.count()).select_from(CustomerDebtLedger).where(*conditions)
        )
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = (
            select(CustomerDebtLedger, Sale.receipt_number)
            .join(Sale, Sale.id == CustomerDebtLedger.sale_id)
            .where(*conditions)
            .order_by(CustomerDebtLedger.incurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(r, receipt_number) for r, receipt_number in rows], total

    async def flush(self) -> None:
        await self._session.flush()
