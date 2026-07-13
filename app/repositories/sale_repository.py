"""Sale repository — DB access for sales, sale_items, payment_types, and the
read-back of the trigger-created debt ledger row.

Strategy A: this repository inserts only the raw sales + sale_items rows. The
database triggers update cash/debt/stock and create the debt ledger entry.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.product import Product
from app.models.sale import CustomerDebtLedger, Sale, SaleItem
from app.models.lookups import PaymentType


class SaleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----- lookups ---------------------------------------------------------- #
    async def get_payment_type_by_code(self, code: str) -> PaymentType | None:
        result = await self._session.execute(
            select(PaymentType).where(PaymentType.code == code)
        )
        return result.scalar_one_or_none()

    async def get_payment_type_by_id(self, type_id: int) -> PaymentType | None:
        result = await self._session.execute(
            select(PaymentType).where(PaymentType.id == type_id)
        )
        return result.scalar_one_or_none()

    async def get_products_by_ids(
        self, business_id: uuid.UUID, product_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Product]:
        if not product_ids:
            return {}
        result = await self._session.execute(
            select(Product).where(
                Product.business_id == business_id,
                Product.id.in_(product_ids),
            )
        )
        return {p.id: p for p in result.scalars().all()}

    async def get_customer(
        self, business_id: uuid.UUID, customer_id: uuid.UUID
    ):
        """Full customer row for debt-sale validation (active/blocked/limit
        checks) — see SaleService.create_sale. Returns the ORM entity so the
        service can read the Customer Credibility Engine columns without an
        extra round trip.
        """
        from app.models.customer import Customer

        result = await self._session.execute(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.business_id == business_id,
            )
        )
        return result.scalar_one_or_none()

    async def customer_belongs_to_business(
        self, business_id: uuid.UUID, customer_id: uuid.UUID
    ) -> bool:
        from app.models.customer import Customer

        result = await self._session.execute(
            select(Customer.id).where(
                Customer.id == customer_id,
                Customer.business_id == business_id,
            )
        )
        return result.scalar_one_or_none() is not None

    # ----- receipt numbering ------------------------------------------------ #
    async def next_receipt_number(
        self, business_id: uuid.UUID, occurred_at: datetime
    ) -> str:
        """Generate RCP-YYYYMMDD-#### scoped to the business and the day.

        The sequence counts that business's existing sales for the same calendar
        day and adds one. Using the day boundary keeps numbers short and
        human-friendly on receipts.
        """
        day = occurred_at.date()
        # Count sales for this business on the same day (by occurred_at date).
        count_stmt = (
            select(func.count())
            .select_from(Sale)
            .where(
                Sale.business_id == business_id,
                func.date(Sale.occurred_at) == day,
            )
        )
        existing = (await self._session.execute(count_stmt)).scalar_one()
        seq = existing + 1
        return f"RCP-{day.strftime('%Y%m%d')}-{seq:04d}"

    # ----- writes (raw events only) ----------------------------------------- #
    async def insert_sale(
        self,
        *,
        business_id: uuid.UUID,
        customer_id: uuid.UUID | None,
        payment_type_id: int,
        total_amount: Decimal,
        total_buying_cost: Decimal,
        outstanding_amount: Decimal,
        due_date: date | None,
        occurred_at: datetime,
        note: str | None,
        receipt_number: str,
    ) -> Sale:
        """Insert the sale header with totals already computed (the trigger reads
        NEW.total_amount), then flush so the FK exists for the items insert.

        ``due_date`` is copied by the database trigger onto the created
        customer_debt_ledger row for ON_DEBT sales (see trg_after_sale_insert);
        it is harmless to set on CASH sales too (the trigger only reads it in
        the ON_DEBT branch), so it is always passed through unconditionally.
        """
        sale = Sale(
            business_id=business_id,
            customer_id=customer_id,
            payment_type_id=payment_type_id,
            total_amount=total_amount,
            total_buying_cost=total_buying_cost,
            outstanding_amount=outstanding_amount,
            due_date=due_date,
            occurred_at=occurred_at,
            note=note,
            receipt_number=receipt_number,
            is_correction=False,
        )
        self._session.add(sale)
        await self._session.flush()
        return sale

    async def set_receipt_shared(
        self, *, sale: Sale, shared_to: str, shared_at: datetime
    ) -> Sale:
        sale.receipt_shared_at = shared_at
        sale.receipt_shared_to = shared_to
        await self._session.flush()
        return sale

    async def set_receipt_number(self, *, sale: Sale, receipt_number: str) -> Sale:
        sale.receipt_number = receipt_number
        await self._session.flush()
        return sale

    async def insert_sale_item(
        self,
        *,
        business_id: uuid.UUID,
        sale_id: uuid.UUID,
        product_id: uuid.UUID | None,
        product_name_snapshot: str,
        quantity: Decimal,
        unit_price: Decimal,
        buying_cost: Decimal | None,
    ) -> SaleItem:
        item = SaleItem(
            business_id=business_id,
            sale_id=sale_id,
            product_id=product_id,
            product_name_snapshot=product_name_snapshot,
            quantity=quantity,
            unit_price=unit_price,
            buying_cost=buying_cost,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_sale_with_items(
        self, business_id: uuid.UUID, sale_id: uuid.UUID
    ) -> Sale | None:
        result = await self._session.execute(
            select(Sale).where(Sale.id == sale_id, Sale.business_id == business_id)
        )
        return result.scalar_one_or_none()

    # ----- reads (list / today) --------------------------------------------- #
    async def list_sales(
        self,
        business_id: uuid.UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        customer_id: uuid.UUID | None = None,
        payment_type_code: str | None = None,
        receipt_number: str | None = None,
        search: str | None = None,
        order: str = "recent",
        limit: int = 50,
        offset: int = 0,
    ) -> list[Sale]:
        """Sales for a business, optionally bounded by occurred_at's calendar
        date (inclusive on both ends) and/or filtered by customer, payment
        type (CASH/ON_DEBT), a receipt-number search (partial match), and/or
        a combined ``search`` matching receipt number OR customer name."""
        conditions = self._list_conditions(
            business_id,
            start_date=start_date,
            end_date=end_date,
            customer_id=customer_id,
            receipt_number=receipt_number,
            search=search,
        )
        stmt = select(Sale).where(*conditions)
        if payment_type_code is not None:
            stmt = stmt.join(
                PaymentType, PaymentType.id == Sale.payment_type_id
            ).where(PaymentType.code == payment_type_code)

        if order == "amount_desc":
            stmt = stmt.order_by(Sale.total_amount.desc())
        elif order == "amount_asc":
            stmt = stmt.order_by(Sale.total_amount.asc())
        elif order == "oldest":
            stmt = stmt.order_by(Sale.occurred_at.asc())
        else:  # recent (default)
            stmt = stmt.order_by(Sale.occurred_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_sales(
        self,
        business_id: uuid.UUID,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        customer_id: uuid.UUID | None = None,
        payment_type_code: str | None = None,
        receipt_number: str | None = None,
        search: str | None = None,
    ) -> int:
        conditions = self._list_conditions(
            business_id,
            start_date=start_date,
            end_date=end_date,
            customer_id=customer_id,
            receipt_number=receipt_number,
            search=search,
        )
        stmt = select(func.count()).select_from(Sale).where(*conditions)
        if payment_type_code is not None:
            stmt = (
                select(func.count())
                .select_from(Sale)
                .join(PaymentType, PaymentType.id == Sale.payment_type_id)
                .where(*conditions, PaymentType.code == payment_type_code)
            )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    @staticmethod
    def _list_conditions(
        business_id: uuid.UUID,
        *,
        start_date: date | None,
        end_date: date | None,
        customer_id: uuid.UUID | None,
        receipt_number: str | None,
        search: str | None = None,
    ) -> list:
        conditions = [Sale.business_id == business_id]
        if start_date is not None:
            conditions.append(func.date(Sale.occurred_at) >= start_date)
        if end_date is not None:
            conditions.append(func.date(Sale.occurred_at) <= end_date)
        if customer_id is not None:
            conditions.append(Sale.customer_id == customer_id)
        if receipt_number is not None:
            conditions.append(Sale.receipt_number.ilike(f"%{receipt_number}%"))
        if search:
            # Single search box: matches receipt_number OR the linked
            # customer's full_name. Kept separate from `receipt_number`
            # above (still supported on its own for backward compatibility)
            # so a frontend already relying on that param keeps working.
            like = f"%{search}%"
            customer_name_match = exists().where(
                Customer.id == Sale.customer_id,
                Customer.full_name.ilike(like),
            )
            conditions.append(
                (Sale.receipt_number.ilike(like)) | customer_name_match
            )
        return conditions

    async def get_ledger_for_sale(
        self, business_id: uuid.UUID, sale_id: uuid.UUID
    ) -> CustomerDebtLedger | None:
        result = await self._session.execute(
            select(CustomerDebtLedger).where(
                CustomerDebtLedger.sale_id == sale_id,
                CustomerDebtLedger.business_id == business_id,
            )
        )
        return result.scalar_one_or_none()

    async def refresh(self, instance) -> None:
        await self._session.refresh(instance)

    async def flush(self) -> None:
        await self._session.flush()