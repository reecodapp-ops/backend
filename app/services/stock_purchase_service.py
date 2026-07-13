"""Stock Purchase service.

TRIGGER-BASED ARCHITECTURE (Strategy A)
---------------------------------------
Triggers automatically:
  * fn_after_stock_purchase_insert         : cash_on_hand -= total_buying_cost
  * fn_after_stock_purchase_item_insert    : stock_on_hand += quantity, last_restocked_at

Service responsibilities (the trigger cannot do these):
  1. Validate the optional supplier belongs to this business.
  2. Validate product_ids that reference an existing product.
  3. Compute total_buying_cost in Python and set it on the header BEFORE insert
     (the header trigger reads NEW.total_buying_cost; it does not sum items).
  4. After items are inserted (stock_on_hand updated by trigger), recompute and
     persist the weighted avg_buying_cost on each affected product. NO trigger
     maintains avg_buying_cost, so the service owns it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.core.business_context import BusinessContext
from app.core.exceptions import NotFoundError, ValidationError
from app.models.product import Product
from app.repositories.stock_purchase_repository import StockPurchaseRepository
from app.services.stock_costing import compute_weighted_avg_buying_cost
from app.schemas.stock_purchase import (
    StockPurchaseCreateRequest,
    StockPurchaseCreateResponse,
    StockPurchaseItemCreate,
    StockPurchaseItemOut,
    StockPurchaseListOut,
    StockPurchaseOut,
    StockPurchaseWarning,
)

# Advisory-only threshold for the PRICE_INCREASE warning (Supplier & Product
# Round spec §5): a new unit cost more than this fraction above the resolved
# benchmark gets flagged. 15% is the V1 default; no central config surface
# exists elsewhere in this codebase for similar thresholds (see
# EXPENSE_RATIO_THRESHOLD in issue_service.py, _UNUSUAL_EXPENSE_MULTIPLIER in
# insights_service.py) -- a plain module constant matches that convention.
PRICE_INCREASE_THRESHOLD = Decimal("0.15")
# How far back "recent" reaches for the price benchmark (matches the Reecod
# Terms Score's own benchmark window -- see ScoringRepository).
PRICE_BENCHMARK_RECENT_DAYS = 180


class StockPurchaseService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = StockPurchaseRepository(ctx.session)

    async def create_purchase(
        self, data: StockPurchaseCreateRequest
    ) -> StockPurchaseCreateResponse:
        biz_id = self._ctx.business_id

        # 1. Validate supplier (if linked).
        if data.supplier_id is not None:
            if not await self._repo.supplier_belongs_to_business(
                biz_id, data.supplier_id
            ):
                raise ValidationError(
                    "Supplier not found for this business.", code="invalid_supplier"
                )

        # 2. Validate products that reference an existing product_id.
        product_ids = [
            item.product_id for item in data.items if item.product_id is not None
        ]
        products = await self._repo.get_products_by_ids(biz_id, product_ids)
        for pid in product_ids:
            if pid not in products:
                raise ValidationError(
                    "One or more products were not found for this business.",
                    code="invalid_product",
                )

        warnings: list[StockPurchaseWarning] = []
        self._collect_duplicate_warnings(data.items, warnings)

        # 3. Compute totals and per-line unit cost.
        total_buying_cost = Decimal("0.00")
        line_plans: list[dict] = []
        # Aggregate qty + cost per product for the avg-cost recompute.
        product_totals: dict[uuid.UUID, dict] = {}
        # Captured BEFORE any insert -- the item trigger increments
        # stock_on_hand as a side effect of the insert, so this is the only
        # point at which "stock immediately prior to this purchase" can be
        # read reliably (reading it back out afterwards and subtracting the
        # purchased qty is fragile: it silently assumes the trigger already
        # ran, which isn't guaranteed the same way across backends/timing).
        stock_before: dict[uuid.UUID, Decimal] = {
            pid: (product.stock_on_hand or Decimal("0")) for pid, product in products.items()
        }

        price_increase_flagged = False
        for item in data.items:
            unit_cost: Decimal | None = None
            if item.quantity > 0:
                unit_cost = (item.total_item_cost / item.quantity).quantize(
                    Decimal("0.01")
                )
            total_buying_cost += item.total_item_cost
            product = products.get(item.product_id) if item.product_id else None
            name_snapshot = product.name if product is not None else item.product_name

            line_plans.append(
                {
                    "product_id": item.product_id,
                    "product_name_snapshot": name_snapshot,
                    "quantity": item.quantity,
                    "quantity_ordered": item.quantity_ordered,
                    "unit_buying_cost": unit_cost,
                    "total_item_cost": item.total_item_cost.quantize(Decimal("0.01")),
                }
            )

            if item.product_id is not None:
                bucket = product_totals.setdefault(
                    item.product_id, {"qty": Decimal("0"), "cost": Decimal("0")}
                )
                bucket["qty"] += item.quantity
                bucket["cost"] += item.total_item_cost

            # PRICE_INCREASE check (spec §5) -- advisory only, never blocks.
            if not price_increase_flagged and unit_cost is not None and item.product_id is not None:
                benchmark = await self._price_benchmark(biz_id, item.product_id, product)
                if benchmark is not None and benchmark > 0:
                    increase_ratio = (unit_cost - benchmark) / benchmark
                    if increase_ratio > PRICE_INCREASE_THRESHOLD:
                        warnings.append(
                            StockPurchaseWarning(
                                code="PRICE_INCREASE",
                                message=(
                                    f"{name_snapshot}'s unit cost ({unit_cost}) is "
                                    f"more than {int(PRICE_INCREASE_THRESHOLD * 100)}% above "
                                    f"the usual price ({benchmark})."
                                ),
                            )
                        )
                        price_increase_flagged = True  # one warning per purchase, not per line

        occurred_at = data.occurred_at or datetime.now(timezone.utc)

        # 4. Insert header (trigger fires, cash decremented), then items.
        purchase = await self._repo.insert_purchase(
            business_id=biz_id,
            supplier_id=data.supplier_id,
            supplier_name_free=data.supplier_name_free,
            total_buying_cost=total_buying_cost,
            occurred_at=occurred_at,
            promised_delivery_date=data.promised_delivery_date,
            actual_delivery_date=data.actual_delivery_date,
            note=data.note,
        )

        for plan in line_plans:
            await self._repo.insert_purchase_item(
                business_id=biz_id,
                purchase_id=purchase.id,
                product_id=plan["product_id"],
                product_name_snapshot=plan["product_name_snapshot"],
                quantity=plan["quantity"],
                quantity_ordered=plan["quantity_ordered"],
                unit_buying_cost=plan["unit_buying_cost"],
                total_item_cost=plan["total_item_cost"],
            )

        # 5. Recompute weighted average buying cost for each product touched.
        # New avg = (old_avg * stock_before + cost_of_this_purchase) / (stock_before + purchased_qty)
        # Uses `stock_before` captured at the very start of this method, NOT
        # a post-insert read-and-subtract -- see the comment where it's
        # captured for why. buying_price is deliberately never written here
        # (Supplier & Product Round spec §6): it's the owner's own reference
        # price, completely separate from this system-only column.
        await self._ctx.session.flush()
        for prod_id, totals in product_totals.items():
            product = products[prod_id]
            purchased_qty: Decimal = totals["qty"]
            purchased_cost: Decimal = totals["cost"]
            prior_stock = stock_before.get(prod_id, Decimal("0"))

            new_avg = compute_weighted_avg_buying_cost(
                old_avg=product.avg_buying_cost,
                old_buying_price=product.buying_price,
                stock_before=prior_stock,
                purchased_qty=purchased_qty,
                purchased_cost=purchased_cost,
            )
            if new_avg is None:
                continue

            await self._repo.update_product_avg_buying_cost(
                product=product,
                new_avg_cost=new_avg,
            )

        await self._ctx.session.flush()
        await self._ctx.session.refresh(purchase)

        return StockPurchaseCreateResponse(
            purchase=self._to_out(purchase), warnings=warnings
        )

    async def list_purchases(
        self,
        *,
        supplier_id: uuid.UUID | None,
        product_id: uuid.UUID | None = None,
        search: str | None = None,
        date_from: datetime | None,
        date_to: datetime | None,
        order: str = "recent",
        limit: int,
        offset: int,
    ) -> StockPurchaseListOut:
        rows, total = await self._repo.list_purchases(
            business_id=self._ctx.business_id,
            supplier_id=supplier_id,
            product_id=product_id,
            search=search,
            date_from=date_from,
            date_to=date_to,
            order=order,
            limit=limit,
            offset=offset,
        )
        return StockPurchaseListOut(
            items=[self._to_out(p) for p in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_purchase(self, purchase_id: uuid.UUID) -> StockPurchaseOut:
        purchase = await self._repo.get_purchase_with_items(
            self._ctx.business_id, purchase_id
        )
        if purchase is None:
            raise NotFoundError(
                "Stock purchase not found.", code="stock_purchase_not_found"
            )
        return self._to_out(purchase)

    # ----- helpers ----------------------------------------------------------- #
    async def _price_benchmark(
        self, business_id: uuid.UUID, product_id: uuid.UUID, product
    ) -> Decimal | None:
        """Resolve a price benchmark using the SAME priority order as the
        Reecod Terms Score's price-fairness factor (spec §4.2):

          1/2. Same item, recent (180-day) average purchase price across
               all suppliers -- ScoringRepository's own benchmark tier,
               reused here via StockPurchaseRepository.get_recent_avg_unit_cost.
          3.   Shop's recent average cost for that item -- products.avg_buying_cost.
          4.   Market benchmark -- no such data source exists in this system,
               so this tier is always skipped.

        Returns ``None`` (skip the check silently, no warning) if no tier
        resolves -- e.g. a brand-new product with no purchase history yet.
        """
        recent_avg = await self._repo.get_recent_avg_unit_cost(
            business_id, product_id, recent_days=PRICE_BENCHMARK_RECENT_DAYS
        )
        if recent_avg is not None:
            return recent_avg
        if product is not None and product.avg_buying_cost is not None:
            return product.avg_buying_cost
        return None

    @staticmethod
    def _collect_duplicate_warnings(
        items: list[StockPurchaseItemCreate],
        warnings: list[StockPurchaseWarning],
    ) -> None:
        seen: set[uuid.UUID] = set()
        flagged: set[uuid.UUID] = set()
        for item in items:
            if item.product_id is None:
                continue
            if item.product_id in seen and item.product_id not in flagged:
                warnings.append(
                    StockPurchaseWarning(
                        code="DUPLICATE_PRODUCT",
                        message=(
                            f"Product appears more than once in this purchase: "
                            f"{item.product_name}."
                        ),
                    )
                )
                flagged.add(item.product_id)
            seen.add(item.product_id)

    @staticmethod
    def _to_out(purchase) -> StockPurchaseOut:
        return StockPurchaseOut(
            id=purchase.id,
            business_id=purchase.business_id,
            supplier_id=purchase.supplier_id,
            supplier_name_free=purchase.supplier_name_free,
            total_buying_cost=purchase.total_buying_cost,
            occurred_at=purchase.occurred_at,
            promised_delivery_date=purchase.promised_delivery_date,
            actual_delivery_date=purchase.actual_delivery_date,
            note=purchase.note,
            is_correction=purchase.is_correction,
            created_at=purchase.created_at,
            items=[
                StockPurchaseItemOut(
                    id=i.id,
                    product_id=i.product_id,
                    product_name_snapshot=i.product_name_snapshot,
                    quantity=i.quantity,
                    quantity_ordered=i.quantity_ordered,
                    unit_buying_cost=i.unit_buying_cost,
                    total_item_cost=i.total_item_cost,
                )
                for i in purchase.items
            ],
        )
