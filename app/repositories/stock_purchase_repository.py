"""Stock purchase repository.

Strategy A: inserts the header (total computed in Python, trigger reads
NEW.total_buying_cost), flushes so the FK exists, then inserts items (each
trigger increments stock_on_hand for the linked product). The header trigger
deducts the total from cash.

Also surfaces product reads (for validation), supplier ownership check, and a
helper for the service-owned avg_buying_cost recomputation.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.stock_purchase import StockPurchase, StockPurchaseItem
from app.models.supplier import Supplier


class StockPurchaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----- validation reads -------------------------------------------------- #
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

    async def supplier_belongs_to_business(
        self, business_id: uuid.UUID, supplier_id: uuid.UUID
    ) -> bool:
        result = await self._session.execute(
            select(Supplier.id).where(
                Supplier.id == supplier_id,
                Supplier.business_id == business_id,
            )
        )
        return result.scalar_one_or_none() is not None

    # ----- writes ----------------------------------------------------------- #
    async def insert_purchase(
        self,
        *,
        business_id: uuid.UUID,
        supplier_id: uuid.UUID | None,
        supplier_name_free: str | None,
        total_buying_cost: Decimal,
        occurred_at: datetime,
        note: str | None,
        promised_delivery_date=None,
        actual_delivery_date=None,
    ) -> StockPurchase:
        purchase = StockPurchase(
            business_id=business_id,
            supplier_id=supplier_id,
            supplier_name_free=supplier_name_free,
            total_buying_cost=total_buying_cost,
            occurred_at=occurred_at,
            promised_delivery_date=promised_delivery_date,
            actual_delivery_date=actual_delivery_date,
            note=note,
            is_correction=False,
        )
        self._session.add(purchase)
        await self._session.flush()
        return purchase

    async def insert_purchase_item(
        self,
        *,
        business_id: uuid.UUID,
        purchase_id: uuid.UUID,
        product_id: uuid.UUID | None,
        product_name_snapshot: str,
        quantity: Decimal,
        unit_buying_cost: Decimal | None,
        total_item_cost: Decimal,
        quantity_ordered: Decimal | None = None,
    ) -> StockPurchaseItem:
        item = StockPurchaseItem(
            business_id=business_id,
            purchase_id=purchase_id,
            product_id=product_id,
            product_name_snapshot=product_name_snapshot,
            quantity=quantity,
            quantity_ordered=quantity_ordered,
            unit_buying_cost=unit_buying_cost,
            total_item_cost=total_item_cost,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def update_product_avg_buying_cost(
        self, *, product: Product, new_avg_cost: Decimal
    ) -> None:
        """Update ONLY product.avg_buying_cost after a purchase. No trigger
        maintains this column — the service owns it. ``buying_price`` is a
        completely separate, owner-set reference price and must NEVER be
        touched here — same system-only vs. owner-editable separation as
        ``customers.credit_score`` vs. the rest of the customer row (see
        Supplier & Product Round spec §6).
        """
        product.avg_buying_cost = new_avg_cost
        await self._session.flush()

    # ----- read ------------------------------------------------------------ #
    async def get_purchase_with_items(
        self, business_id: uuid.UUID, purchase_id: uuid.UUID
    ) -> StockPurchase | None:
        result = await self._session.execute(
            select(StockPurchase).where(
                StockPurchase.id == purchase_id,
                StockPurchase.business_id == business_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_purchases(
        self,
        *,
        business_id: uuid.UUID,
        supplier_id: uuid.UUID | None,
        product_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: datetime | None,
        date_to: datetime | None,
        order: str = "recent",
        limit: int,
        offset: int,
    ) -> tuple[list[StockPurchase], int]:
        conditions = [StockPurchase.business_id == business_id]
        if supplier_id is not None:
            conditions.append(StockPurchase.supplier_id == supplier_id)
        if search:
            from sqlalchemy import or_

            conditions.append(
                or_(
                    StockPurchase.supplier_name_free.ilike(f"%{search}%"),
                    StockPurchase.note.ilike(f"%{search}%"),
                )
            )
        if date_from is not None:
            conditions.append(StockPurchase.occurred_at >= date_from)
        if date_to is not None:
            conditions.append(StockPurchase.occurred_at <= date_to)

        # product_id filters on the child items table -- EXISTS avoids
        # duplicating a purchase row per matching line item.
        base_query_from = StockPurchase
        if product_id is not None:
            product_match = exists().where(
                StockPurchaseItem.purchase_id == StockPurchase.id,
                StockPurchaseItem.product_id == product_id,
            )
            conditions.append(product_match)

        count_stmt = select(func.count()).select_from(base_query_from).where(*conditions)
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = select(StockPurchase).where(*conditions)
        if order == "cost_desc":
            stmt = stmt.order_by(StockPurchase.total_buying_cost.desc())
        elif order == "cost_asc":
            stmt = stmt.order_by(StockPurchase.total_buying_cost.asc())
        elif order == "oldest":
            stmt = stmt.order_by(StockPurchase.occurred_at.asc())
        else:  # recent
            stmt = stmt.order_by(StockPurchase.occurred_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows), total

    async def get_recent_avg_unit_cost(
        self, business_id: uuid.UUID, product_id: uuid.UUID, *, recent_days: int = 180
    ) -> Decimal | None:
        """Tier 1/2 of the Reecod Terms Score's price-benchmark priority
        order (spec §4.2): the shop-wide recent average unit_buying_cost for
        this exact product, across all suppliers, in the last
        ``recent_days``. Returns ``None`` if there's no such purchase in the
        window -- caller falls back to ``product.avg_buying_cost`` (tier 3),
        per the PRICE_INCREASE check's benchmark priority.
        """
        from datetime import date, timedelta

        cutoff = date.today() - timedelta(days=recent_days)
        stmt = (
            select(func.avg(StockPurchaseItem.unit_buying_cost))
            .select_from(StockPurchaseItem)
            .join(StockPurchase, StockPurchase.id == StockPurchaseItem.purchase_id)
            .where(
                StockPurchase.business_id == business_id,
                StockPurchaseItem.product_id == product_id,
                StockPurchaseItem.unit_buying_cost.is_not(None),
                StockPurchase.is_correction.is_(False),
                func.date(StockPurchase.occurred_at) >= cutoff,
            )
        )
        avg_cost = (await self._session.execute(stmt)).scalar_one()
        return Decimal(str(avg_cost)) if avg_cost is not None else None

    async def refresh(self, instance) -> None:
        await self._session.refresh(instance)

    async def flush(self) -> None:
        await self._session.flush()
