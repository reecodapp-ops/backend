"""Product & Category service.

Categories: create (custom, per business), list (system defaults + own), update.
Products:   create, update, detail, search, archive.

Soft-warning rule (blueprint Validation-First): on product create, if an active
product with a closely matching name already exists, return a DUPLICATE_ITEM
warning alongside the created product — never block.

Price-edit rule (blueprint F5): changing buying_price updates the product's
current buying_price and avg_buying_cost baseline, but NEVER rewrites historical
sale_items.buying_cost snapshots. A PRODUCT_COST_EDIT correction-log entry is the
responsibility of the Corrections module (added later); here we only update the
master record so margin math on past sales stays accurate.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.core.business_context import BusinessContext
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.product import Product, ProductCategory
from app.repositories.product_repository import (
    CategoryRepository,
    ProductRepository,
)
from app.repositories.stock_purchase_repository import StockPurchaseRepository
from app.schemas.product import (
    CategoryCreateRequest,
    CategoryOut,
    CategoryUpdateRequest,
    ProductCreateRequest,
    ProductCreateResponse,
    ProductListOut,
    ProductOut,
    ProductUpdateRequest,
    ProductWarning,
)
from app.services.stock_costing import compute_weighted_avg_buying_cost

OPENING_STOCK_NOTE = "Opening stock"


class CategoryService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = CategoryRepository(ctx.session)

    async def create(self, data: CategoryCreateRequest) -> CategoryOut:
        existing = await self._repo.get_by_name(self._ctx.business_id, data.name)
        if existing is not None:
            raise ConflictError(
                "A category with this name already exists.",
                code="category_name_taken",
            )
        category = await self._repo.create(
            business_id=self._ctx.business_id,
            name=data.name,
            icon_name=data.icon_name,
            color_hex=data.color_hex,
            sort_order=data.sort_order,
        )
        return CategoryOut.model_validate(category)

    async def list(self, include_inactive: bool = False) -> list[CategoryOut]:
        rows = await self._repo.list_visible(
            self._ctx.business_id, include_inactive=include_inactive
        )
        return [CategoryOut.model_validate(c) for c in rows]

    async def update(
        self, category_id: int, data: CategoryUpdateRequest
    ) -> CategoryOut:
        category = await self._get_owned_or_404(category_id)
        if data.name is not None:
            category.name = data.name
        if data.icon_name is not None:
            category.icon_name = data.icon_name
        if data.color_hex is not None:
            category.color_hex = data.color_hex
        if data.sort_order is not None:
            category.sort_order = data.sort_order
        if data.is_active is not None:
            category.is_active = data.is_active
        await self._repo.flush()
        await self._ctx.session.refresh(category)
        return CategoryOut.model_validate(category)

    async def _get_owned_or_404(self, category_id: int) -> ProductCategory:
        category = await self._repo.get_by_id(category_id)
        if category is None:
            raise NotFoundError("Category not found.", code="category_not_found")
        # System defaults (business_id IS NULL) are read-only to businesses.
        if category.business_id is None:
            raise ValidationError(
                "System default categories cannot be edited.",
                code="system_category_readonly",
            )
        if category.business_id != self._ctx.business_id:
            raise NotFoundError("Category not found.", code="category_not_found")
        return category


class ProductService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = ProductRepository(ctx.session)
        self._categories = CategoryRepository(ctx.session)
        self._stock_purchases = StockPurchaseRepository(ctx.session)

    async def create(self, data: ProductCreateRequest) -> ProductCreateResponse:
        await self._validate_category(data.category_id)

        warnings: list[ProductWarning] = []
        similar = await self._repo.find_similar_names(
            self._ctx.business_id, data.name
        )
        if similar:
            names = ", ".join(p.name for p in similar[:3])
            warnings.append(
                ProductWarning(
                    code="DUPLICATE_ITEM",
                    message=f"Similar item(s) already exist: {names}.",
                )
            )

        # stock_on_hand always starts at zero here -- see ProductRepository
        # .create's own comment. A requested starting quantity is recorded as
        # a real (synthetic) "Opening stock" purchase below instead, so there
        # is always a ledger entry explaining where a product's stock came
        # from -- same pattern already used for OPENING_BALANCE on the cash/
        # capital side (Supplier & Product Round spec §7).
        product = await self._repo.create(
            business_id=self._ctx.business_id,
            category_id=data.category_id,
            name=data.name,
            unit=data.unit,
            selling_price=data.selling_price,
            buying_price=data.buying_price,
            low_stock_level=data.low_stock_level,
            image_url=data.image_url,
            image_thumbnail_url=data.image_thumbnail_url,
        )

        if data.stock_qty > 0:
            await self._create_opening_stock_purchase(product, data.stock_qty, data.buying_price)

        return ProductCreateResponse(
            product=ProductOut.model_validate(product), warnings=warnings
        )

    async def _create_opening_stock_purchase(
        self, product: Product, quantity: Decimal, buying_price: Decimal | None
    ) -> None:
        """Insert a synthetic ``stock_purchases`` + one ``stock_purchase_items``
        row for a brand-new product's starting quantity, instead of setting
        ``stock_on_hand`` directly. ``unit_buying_cost`` uses the given
        ``buying_price`` if the owner provided one, or 0 -- never a
        fabricated cost that wasn't actually given (spec §7 step 3). The
        stock_purchase_items insert trigger is what brings the product's
        stock_on_hand up to ``quantity``, same as any other restock; this
        method never touches stock_on_hand itself.
        """
        unit_cost = buying_price if buying_price is not None else Decimal("0")
        total_cost = (quantity * unit_cost).quantize(Decimal("0.01"))

        purchase = await self._stock_purchases.insert_purchase(
            business_id=self._ctx.business_id,
            supplier_id=None,
            supplier_name_free=None,
            total_buying_cost=total_cost,
            occurred_at=datetime.now(timezone.utc),
            note=OPENING_STOCK_NOTE,
        )
        await self._stock_purchases.insert_purchase_item(
            business_id=self._ctx.business_id,
            purchase_id=purchase.id,
            product_id=product.id,
            product_name_snapshot=product.name,
            quantity=quantity,
            unit_buying_cost=unit_cost,
            total_item_cost=total_cost,
        )

        # Same weighted-average recompute a real purchase gets -- for a
        # brand-new product this is simply new_avg = unit_cost, but routing
        # it through the shared formula (rather than special-casing "first
        # purchase") keeps this one code path the only place avg_buying_cost
        # is ever written.
        new_avg = compute_weighted_avg_buying_cost(
            old_avg=product.avg_buying_cost,
            old_buying_price=product.buying_price,
            stock_before=Decimal("0"),
            purchased_qty=quantity,
            purchased_cost=total_cost,
        )
        if new_avg is not None:
            await self._stock_purchases.update_product_avg_buying_cost(
                product=product, new_avg_cost=new_avg
            )
        await self._ctx.session.refresh(product)

    async def update(
        self, product_id: uuid.UUID, data: ProductUpdateRequest
    ) -> ProductOut:
        product = await self._get_or_404(product_id)
        if data.category_id is not None:
            await self._validate_category(data.category_id)
            product.category_id = data.category_id
        if data.name is not None:
            product.name = data.name
        if data.unit is not None:
            product.unit = data.unit
        if data.selling_price is not None:
            product.selling_price = data.selling_price
        if data.buying_price is not None:
            # Update the owner-editable reference price only. Historical
            # sale_items.buying_cost snapshots are intentionally left
            # untouched (margin accuracy on past sales). avg_buying_cost is
            # NOT synced here -- it's a system-only column, purely derived
            # from actual purchase history by StockPurchaseService's
            # weighted-average recompute, same separation as
            # customers.credit_score (system-only) vs. owner-editable
            # customer fields (Supplier & Product Round spec §6). Letting a
            # manual buying_price edit overwrite it would corrupt that
            # derived history. A PRODUCT_COST_EDIT correction is logged by
            # the Corrections module when that module is present.
            product.buying_price = data.buying_price
        if data.low_stock_level is not None:
            product.low_stock_level = data.low_stock_level
        if data.image_url is not None:
            product.image_url = data.image_url
        if data.image_thumbnail_url is not None:
            product.image_thumbnail_url = data.image_thumbnail_url
        if data.status is not None:
            product.status = data.status
        product.updated_at = datetime.now(timezone.utc)
        await self._repo.flush()
        await self._ctx.session.refresh(product)
        return ProductOut.model_validate(product)

    async def archive(self, product_id: uuid.UUID) -> ProductOut:
        product = await self._get_or_404(product_id)
        product.status = "ARCHIVED"
        product.updated_at = datetime.now(timezone.utc)
        await self._repo.flush()
        await self._ctx.session.refresh(product)
        return ProductOut.model_validate(product)

    async def get(self, product_id: uuid.UUID) -> ProductOut:
        product = await self._get_or_404(product_id)
        return ProductOut.model_validate(product)

    async def search(
        self,
        *,
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
    ) -> ProductListOut:
        rows, total = await self._repo.search(
            business_id=self._ctx.business_id,
            search=search,
            category_id=category_id,
            status=status,
            stock_status=stock_status,
            min_price=min_price,
            max_price=max_price,
            supplier_id=supplier_id,
            order=order,
            limit=limit,
            offset=offset,
        )
        return ProductListOut(
            items=[ProductOut.model_validate(p) for p in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def _validate_category(self, category_id: int | None) -> None:
        if category_id is None:
            return
        category = await self._categories.get_visible_by_id(
            self._ctx.business_id, category_id
        )
        if category is None:
            raise ValidationError(
                "Unknown or inaccessible category_id.",
                code="invalid_category",
            )

    async def _get_or_404(self, product_id: uuid.UUID) -> Product:
        product = await self._repo.get_by_id(self._ctx.business_id, product_id)
        if product is None:
            raise NotFoundError("Product not found.", code="product_not_found")
        return product
