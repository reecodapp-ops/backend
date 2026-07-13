"""Product & category repository — DB access for products and product_categories.

Category listing returns BOTH system defaults (business_id IS NULL) and the
calling business's own categories, per the blueprint.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product, ProductCategory


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, category_id: int) -> ProductCategory | None:
        result = await self._session.execute(
            select(ProductCategory).where(ProductCategory.id == category_id)
        )
        return result.scalar_one_or_none()

    async def get_visible_by_id(
        self, business_id: uuid.UUID, category_id: int
    ) -> ProductCategory | None:
        """A category usable by this business: its own or a system default."""
        result = await self._session.execute(
            select(ProductCategory).where(
                ProductCategory.id == category_id,
                or_(
                    ProductCategory.business_id == business_id,
                    ProductCategory.business_id.is_(None),
                ),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_name(
        self, business_id: uuid.UUID, name: str
    ) -> ProductCategory | None:
        result = await self._session.execute(
            select(ProductCategory).where(
                ProductCategory.business_id == business_id,
                func.lower(ProductCategory.name) == name.lower(),
            )
        )
        return result.scalars().first()

    async def create(
        self,
        *,
        business_id: uuid.UUID,
        name: str,
        icon_name: str | None,
        color_hex: str | None,
        sort_order: int,
    ) -> ProductCategory:
        category = ProductCategory(
            business_id=business_id,
            name=name,
            icon_name=icon_name,
            color_hex=color_hex,
            sort_order=sort_order,
            is_system=False,
            is_active=True,
        )
        self._session.add(category)
        await self._session.flush()
        await self._session.refresh(category)
        return category

    async def list_visible(
        self, business_id: uuid.UUID, include_inactive: bool = False
    ) -> list[ProductCategory]:
        conditions = [
            or_(
                ProductCategory.business_id == business_id,
                ProductCategory.business_id.is_(None),
            )
        ]
        if not include_inactive:
            conditions.append(ProductCategory.is_active.is_(True))
        stmt = (
            select(ProductCategory)
            .where(*conditions)
            .order_by(ProductCategory.sort_order.asc(), ProductCategory.name.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)

    async def flush(self) -> None:
        await self._session.flush()


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self, business_id: uuid.UUID, product_id: uuid.UUID
    ) -> Product | None:
        result = await self._session.execute(
            select(Product).where(
                Product.id == product_id,
                Product.business_id == business_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_name(
        self, business_id: uuid.UUID, name: str
    ) -> Product | None:
        result = await self._session.execute(
            select(Product).where(
                Product.business_id == business_id,
                func.lower(Product.name) == name.lower(),
            )
        )
        return result.scalars().first()

    async def find_similar_names(
        self, business_id: uuid.UUID, name: str
    ) -> list[Product]:
        """Find active products with a similar name for the Duplicate Item
        soft warning.

        Matches in both directions plus on a shared leading token so that
        "Soap Bar" and "Soap Bar Large" are recognised as potential duplicates:
          - existing name contains the new name, OR
          - existing name starts with the new name's first word (token).
        """
        first_token = name.strip().split()[0] if name.strip() else name
        result = await self._session.execute(
            select(Product).where(
                Product.business_id == business_id,
                Product.status == "ACTIVE",
                or_(
                    Product.name.ilike(f"%{name}%"),
                    Product.name.ilike(f"{first_token}%"),
                ),
            )
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        business_id: uuid.UUID,
        category_id: int | None,
        name: str,
        unit: str | None,
        selling_price: Decimal | None,
        buying_price: Decimal | None,
        low_stock_level: Decimal,
        image_url: str | None,
        image_thumbnail_url: str | None,
    ) -> Product:
        product = Product(
            business_id=business_id,
            category_id=category_id,
            name=name,
            unit=unit,
            selling_price=selling_price,
            buying_price=buying_price,
            # [CHANGED -- Supplier & Product Round §7] Always starts at zero.
            # An initial quantity is no longer set directly here -- it's
            # recorded as a synthetic "Opening stock" purchase by
            # ProductService.create, and the stock_purchase_items insert
            # trigger is what brings stock_on_hand up to the requested
            # value, same as any other restock. Never set stock_on_hand
            # outside that flow (or a sale).
            stock_on_hand=Decimal("0"),
            low_stock_level=low_stock_level,
            # avg_buying_cost is left NULL here (system-only, purchase-
            # derived column -- see app/services/stock_costing.py). It gets
            # its first value from the opening-stock purchase's own
            # weighted-average recompute, same code path as any other
            # purchase; readers that need a cost before any purchase exists
            # fall back to buying_price themselves (see
            # SaleService._resolve_buying_cost and
            # StockPurchaseService/stock_costing's own old_buying_price
            # fallback).
            avg_buying_cost=None,
            image_url=image_url,
            image_thumbnail_url=image_thumbnail_url,
            status="ACTIVE",
        )
        self._session.add(product)
        await self._session.flush()
        await self._session.refresh(product)
        return product

    async def search(
        self,
        *,
        business_id: uuid.UUID,
        search: str | None,
        category_id: int | None,
        status: str | None,
        stock_status: str | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        supplier_id: uuid.UUID | None = None,
        order: str = "name_asc",
        limit: int,
        offset: int,
    ) -> tuple[list[Product], int]:
        """``stock_status`` accepts OUT_OF_STOCK / LOW_STOCK / OK, matching the
        buckets in ``v_stock_attention`` (SLOW_MOVER is a time-based bucket
        computed by that view from ``last_sold_at`` and is intentionally not
        duplicated here — see DashboardRepository.get_stock_attention).
        ``supplier_id`` matches any product that has at least one stock
        purchase line from that supplier (there is no direct product-supplier
        FK in the schema — suppliers are linked via stock purchases).
        """
        conditions = [Product.business_id == business_id]
        if status:
            conditions.append(Product.status == status)
        if category_id is not None:
            conditions.append(Product.category_id == category_id)
        if search:
            conditions.append(Product.name.ilike(f"%{search}%"))
        if min_price is not None:
            conditions.append(Product.selling_price >= min_price)
        if max_price is not None:
            conditions.append(Product.selling_price <= max_price)
        if stock_status == "OUT_OF_STOCK":
            conditions.append(Product.stock_on_hand <= 0)
        elif stock_status == "LOW_STOCK":
            conditions.append(
                Product.stock_on_hand > 0,
            )
            conditions.append(Product.stock_on_hand <= Product.low_stock_level)
        elif stock_status == "OK":
            conditions.append(Product.stock_on_hand > Product.low_stock_level)
        if supplier_id is not None:
            from app.models.stock_purchase import StockPurchase, StockPurchaseItem

            conditions.append(
                exists().where(
                    StockPurchaseItem.product_id == Product.id,
                    StockPurchaseItem.purchase_id == StockPurchase.id,
                    StockPurchase.supplier_id == supplier_id,
                )
            )

        count_stmt = select(func.count()).select_from(Product).where(*conditions)
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = select(Product).where(*conditions)
        if order == "stock_asc":
            stmt = stmt.order_by(Product.stock_on_hand.asc())
        elif order == "stock_desc":
            stmt = stmt.order_by(Product.stock_on_hand.desc())
        elif order == "price_asc":
            stmt = stmt.order_by(Product.selling_price.asc().nulls_last())
        elif order == "price_desc":
            stmt = stmt.order_by(Product.selling_price.desc().nulls_last())
        elif order == "recent":
            stmt = stmt.order_by(Product.created_at.desc())
        else:  # name_asc (default)
            stmt = stmt.order_by(Product.name.asc())
        stmt = stmt.limit(limit).offset(offset)

        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows), total

    async def flush(self) -> None:
        await self._session.flush()
