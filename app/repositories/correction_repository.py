"""Correction repository.

Encapsulates: original-record fetch (across the five entity tables), flipping
the original's ``is_correction`` flag, inserting the replacement row via the
appropriate model, writing the audit row, and triggering the schema's
``reconcile_business_cash`` function.

The replacement insert intentionally goes through the same models as the
business modules so any constraint or default behaves identically. The flipped
original is excluded from reconcile (it filters ``is_correction = FALSE``), the
replacement is included.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import CapitalTransaction
from app.models.correction import Correction, CorrectionType
from app.models.expense import Expense
from app.models.payment import CustomerPayment
from app.models.sale import Sale


class CorrectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----- lookups ---------------------------------------------------------- #
    async def get_correction_type_by_code(
        self, code: str
    ) -> CorrectionType | None:
        result = await self._session.execute(
            select(CorrectionType).where(CorrectionType.code == code)
        )
        return result.scalar_one_or_none()

    # ----- original-record fetchers ---------------------------------------- #
    async def get_sale(
        self, business_id: uuid.UUID, sale_id: uuid.UUID
    ) -> Sale | None:
        result = await self._session.execute(
            select(Sale).where(Sale.id == sale_id, Sale.business_id == business_id)
        )
        return result.scalar_one_or_none()

    async def get_expense(
        self, business_id: uuid.UUID, expense_id: uuid.UUID
    ) -> Expense | None:
        result = await self._session.execute(
            select(Expense).where(
                Expense.id == expense_id, Expense.business_id == business_id
            )
        )
        return result.scalar_one_or_none()

    async def get_customer_payment(
        self, business_id: uuid.UUID, payment_id: uuid.UUID
    ) -> CustomerPayment | None:
        result = await self._session.execute(
            select(CustomerPayment).where(
                CustomerPayment.id == payment_id,
                CustomerPayment.business_id == business_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_capital_transaction(
        self, business_id: uuid.UUID, txn_id: uuid.UUID
    ) -> CapitalTransaction | None:
        result = await self._session.execute(
            select(CapitalTransaction).where(
                CapitalTransaction.id == txn_id,
                CapitalTransaction.business_id == business_id,
            )
        )
        return result.scalar_one_or_none()

    # ----- flip original ---------------------------------------------------- #
    async def flag_sale_corrected(self, sale_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Sale).where(Sale.id == sale_id).values(
                is_correction=True, updated_at=datetime.now(timezone.utc)
            )
        )

    async def flag_expense_corrected(self, expense_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Expense).where(Expense.id == expense_id).values(
                is_correction=True, updated_at=datetime.now(timezone.utc)
            )
        )

    async def flag_payment_corrected(self, payment_id: uuid.UUID) -> None:
        await self._session.execute(
            update(CustomerPayment).where(CustomerPayment.id == payment_id).values(
                is_correction=True, updated_at=datetime.now(timezone.utc)
            )
        )

    async def flag_capital_corrected(self, txn_id: uuid.UUID) -> None:
        await self._session.execute(
            update(CapitalTransaction).where(CapitalTransaction.id == txn_id).values(
                is_correction=True, updated_at=datetime.now(timezone.utc)
            )
        )

    # ----- replacement inserts --------------------------------------------- #
    async def insert_expense_replacement(
        self,
        *,
        business_id: uuid.UUID,
        original: Expense,
        new_amount: Decimal,
        new_category_id: int | None,
        new_note: str | None,
        new_supplier_name: str | None,
        occurred_at: datetime,
    ) -> Expense:
        replacement = Expense(
            business_id=business_id,
            category_id=new_category_id or original.category_id,
            amount=new_amount,
            occurred_at=occurred_at,
            note=new_note if new_note is not None else original.note,
            supplier_name=(
                new_supplier_name if new_supplier_name is not None else original.supplier_name
            ),
            is_correction=False,
            original_expense_id=original.id,
        )
        self._session.add(replacement)
        await self._session.flush()
        return replacement

    async def insert_payment_replacement(
        self,
        *,
        business_id: uuid.UUID,
        original: CustomerPayment,
        new_amount: Decimal,
        new_note: str | None,
        occurred_at: datetime,
    ) -> CustomerPayment:
        replacement = CustomerPayment(
            business_id=business_id,
            customer_id=original.customer_id,
            amount=new_amount,
            occurred_at=occurred_at,
            note=new_note if new_note is not None else original.note,
            is_correction=False,
            original_payment_id=original.id,
        )
        self._session.add(replacement)
        await self._session.flush()
        return replacement

    async def insert_capital_replacement(
        self,
        *,
        business_id: uuid.UUID,
        original: CapitalTransaction,
        new_amount: Decimal,
        new_source_type_id: int | None,
        new_note: str | None,
        occurred_at: datetime,
    ) -> CapitalTransaction:
        replacement = CapitalTransaction(
            business_id=business_id,
            type_id=original.type_id,
            source_type_id=(
                new_source_type_id
                if new_source_type_id is not None
                else original.source_type_id
            ),
            amount=new_amount,
            occurred_at=occurred_at,
            note=new_note if new_note is not None else original.note,
            is_correction=False,
            original_txn_id=original.id,
        )
        self._session.add(replacement)
        await self._session.flush()
        return replacement

    async def insert_sale_total_replacement(
        self,
        *,
        business_id: uuid.UUID,
        original: Sale,
        new_total_amount: Decimal,
        occurred_at: datetime,
    ) -> Sale:
        """SALE_EDIT here covers a header-level total adjustment only (e.g.
        wrong total entered). Line-item edits (SALE_ITEM_EDIT) require stock
        repair and are intentionally out of scope.

        outstanding_amount on the replacement is set the same way Sale creation
        does: full new_total_amount for ON_DEBT, 0 for CASH. Payment_type_id 2
        is ON_DEBT in the seeded payment_types lookup.
        """
        outstanding = (
            new_total_amount if original.payment_type_id == 2 else Decimal("0.00")
        )
        replacement = Sale(
            business_id=business_id,
            customer_id=original.customer_id,
            payment_type_id=original.payment_type_id,
            total_amount=new_total_amount,
            total_buying_cost=original.total_buying_cost,  # snapshot preserved
            outstanding_amount=outstanding,
            occurred_at=occurred_at,
            note=original.note,
            receipt_number=original.receipt_number,
            is_correction=False,
            original_sale_id=original.id,
        )
        self._session.add(replacement)
        await self._session.flush()
        return replacement

    # ----- audit row -------------------------------------------------------- #
    async def insert_correction_audit(
        self,
        *,
        business_id: uuid.UUID,
        correction_type_id: int,
        original_record_id: uuid.UUID,
        corrected_record_id: uuid.UUID,
        field_changed: str | None,
        old_value_text: str | None,
        new_value_text: str | None,
        reason: str,
    ) -> Correction:
        audit = Correction(
            business_id=business_id,
            correction_type_id=correction_type_id,
            original_record_id=original_record_id,
            corrected_record_id=corrected_record_id,
            field_changed=field_changed,
            old_value_text=old_value_text,
            new_value_text=new_value_text,
            reason=reason,
        )
        self._session.add(audit)
        await self._session.flush()
        return audit

    # ----- reconcile + read ------------------------------------------------- #
    async def reconcile_business_cash(self, business_id: uuid.UUID) -> None:
        """Invoke the schema's reconcile function (PostgreSQL only).

        On SQLite the function does not exist, so reconciliation is a no-op in
        tests; the corrections themselves are still recorded and the original's
        ``is_correction`` flag is flipped, so the audit trail is identical."""
        bind = self._session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            await self._session.execute(
                text("SELECT reconcile_business_cash(:bid)"),
                {"bid": str(business_id)},
            )

    async def get_business_cash_and_debt(
        self, business_id: uuid.UUID
    ) -> tuple[Decimal, Decimal] | None:
        from app.models.business import Business

        result = await self._session.execute(
            select(Business.cash_on_hand, Business.total_debt_out).where(
                Business.id == business_id
            )
        )
        row = result.first()
        if row is None:
            return None
        return row[0], row[1]

    async def list_corrections(
        self,
        *,
        business_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[Correction, CorrectionType]], int]:
        from sqlalchemy import func

        base = (
            select(Correction, CorrectionType)
            .join(CorrectionType, CorrectionType.id == Correction.correction_type_id)
            .where(Correction.business_id == business_id)
        )
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()
        stmt = base.order_by(Correction.created_at.desc()).limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).all()
        return [(c, ct) for c, ct in rows], total

    async def flush(self) -> None:
        await self._session.flush()
