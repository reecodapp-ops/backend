"""Customer payment repository.

Strategy A: inserts only the customer_payments row; the trigger creates the
allocations and updates balances. This repository also reads those trigger
results back (allocations joined to their ledger rows) and lists payment history.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.payment import CustomerPayment, PaymentAllocation
from app.models.sale import CustomerDebtLedger


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----- validation reads -------------------------------------------------- #
    async def get_customer(
        self, business_id: uuid.UUID, customer_id: uuid.UUID
    ) -> Customer | None:
        result = await self._session.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.business_id == business_id,
            )
        )
        return result.scalar_one_or_none()

    # ----- write (raw event only) ------------------------------------------- #
    async def insert_payment(
        self,
        *,
        business_id: uuid.UUID,
        customer_id: uuid.UUID,
        amount: Decimal,
        occurred_at: datetime,
        note: str | None,
    ) -> CustomerPayment:
        payment = CustomerPayment(
            business_id=business_id,
            customer_id=customer_id,
            amount=amount,
            occurred_at=occurred_at,
            note=note,
            is_correction=False,
        )
        self._session.add(payment)
        await self._session.flush()
        return payment

    # ----- trigger-result reads --------------------------------------------- #
    async def get_allocations_for_payment(
        self, business_id: uuid.UUID, payment_id: uuid.UUID
    ) -> list[tuple[PaymentAllocation, CustomerDebtLedger]]:
        """Return each allocation joined to its debt-ledger row, so the response
        can include sale_id, ledger status and remaining amount."""
        stmt = (
            select(PaymentAllocation, CustomerDebtLedger)
            .join(
                CustomerDebtLedger,
                CustomerDebtLedger.id == PaymentAllocation.debt_ledger_id,
            )
            .where(
                PaymentAllocation.payment_id == payment_id,
                PaymentAllocation.business_id == business_id,
            )
            .order_by(CustomerDebtLedger.incurred_at.asc())
        )
        rows = await self._session.execute(stmt)
        return [(a, l) for a, l in rows.all()]

    async def get_allocations_for_ledger_ids(
        self, business_id: uuid.UUID, ledger_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[tuple[PaymentAllocation, CustomerPayment]]]:
        """For each of the given debt-ledger rows, which payment(s) were
        allocated against it -- the inverse direction of
        ``get_allocations_for_payment``. One bulk query (no N+1) regardless
        of how many ledger rows are requested; empty ``ledger_ids`` returns
        ``{}`` without hitting the database.
        """
        if not ledger_ids:
            return {}
        stmt = (
            select(PaymentAllocation, CustomerPayment)
            .join(
                CustomerPayment,
                CustomerPayment.id == PaymentAllocation.payment_id,
            )
            .where(
                PaymentAllocation.business_id == business_id,
                PaymentAllocation.debt_ledger_id.in_(ledger_ids),
            )
            .order_by(CustomerPayment.occurred_at.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        by_ledger: dict[uuid.UUID, list[tuple[PaymentAllocation, CustomerPayment]]] = {
            lid: [] for lid in ledger_ids
        }
        for alloc, payment in rows:
            by_ledger.setdefault(alloc.debt_ledger_id, []).append((alloc, payment))
        return by_ledger

    async def get_customer_debt_balance(
        self, business_id: uuid.UUID, customer_id: uuid.UUID
    ) -> Decimal:
        result = await self._session.execute(
            select(Customer.debt_balance).where(
                Customer.id == customer_id,
                Customer.business_id == business_id,
            )
        )
        value = result.scalar_one_or_none()
        return value if value is not None else Decimal("0")

    # ----- history ---------------------------------------------------------- #
    async def list_payments(
        self,
        *,
        business_id: uuid.UUID,
        customer_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[CustomerPayment], int]:
        conditions = [CustomerPayment.business_id == business_id]
        if customer_id is not None:
            conditions.append(CustomerPayment.customer_id == customer_id)

        count_stmt = (
            select(func.count()).select_from(CustomerPayment).where(*conditions)
        )
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = (
            select(CustomerPayment)
            .where(*conditions)
            .order_by(CustomerPayment.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows), total

    async def list_payments_with_allocations(
        self,
        *,
        business_id: uuid.UUID,
        customer_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[CustomerPayment, list[tuple[PaymentAllocation, CustomerDebtLedger]]]], int]:
        """Payment history for one customer, each payment paired with exactly
        which debt-ledger entries it was allocated to (oldest-first, per the
        allocation trigger). Two queries total (payments page, then a single
        bulk allocations query for that page) rather than one per payment.
        """
        payments, total = await self.list_payments(
            business_id=business_id,
            customer_id=customer_id,
            limit=limit,
            offset=offset,
        )
        if not payments:
            return [], total

        payment_ids = [p.id for p in payments]
        alloc_stmt = (
            select(PaymentAllocation, CustomerDebtLedger)
            .join(
                CustomerDebtLedger,
                CustomerDebtLedger.id == PaymentAllocation.debt_ledger_id,
            )
            .where(
                PaymentAllocation.business_id == business_id,
                PaymentAllocation.payment_id.in_(payment_ids),
            )
            .order_by(CustomerDebtLedger.incurred_at.asc())
        )
        alloc_rows = (await self._session.execute(alloc_stmt)).all()

        by_payment: dict[uuid.UUID, list[tuple[PaymentAllocation, CustomerDebtLedger]]] = {
            pid: [] for pid in payment_ids
        }
        for alloc, ledger in alloc_rows:
            by_payment.setdefault(alloc.payment_id, []).append((alloc, ledger))

        return [(p, by_payment.get(p.id, [])) for p in payments], total

    async def refresh(self, instance) -> None:
        await self._session.refresh(instance)

    async def flush(self) -> None:
        await self._session.flush()
