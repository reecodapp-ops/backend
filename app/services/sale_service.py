"""Sale service — cash and debt sales.

TRIGGER-BASED ARCHITECTURE (Strategy A)
---------------------------------------
This service inserts ONLY the raw sales + sale_items rows. It never updates
cash_on_hand, debt_balance, total_debt_out, stock_on_hand, the debt ledger, or
any Customer Credibility Engine column — those are owned by the database
triggers/functions (fn_after_sale_insert, fn_after_sale_item_insert,
recompute_customer_credit_score). Doing so here would double-count or
contradict the single source of truth for the credit score.

Service responsibilities (the parts triggers cannot do):
  1. Validate every product_id belongs to the business (invalid_product).
  2. For ON_DEBT: the customer must exist, belong to this business, be ACTIVE,
     and not be credit-blocked — all hard validation failures (422). This is
     the Customer Credibility Engine's "Debt Sale" contract: existence/active/
     not-blocked are gates; the DB itself also enforces "must have a
     customer" via trg_validate_sale as a fail-safe.
  3. If the sale would push the customer's debt over their
     recommended_credit_limit, add a SOFT warning (CREDIT_LIMIT_EXCEEDED) —
     never reject. The frontend decides whether to proceed. A customer with
     no credit history yet (credit_level == UNSCORED) gets an informational
     NEW_CUSTOMER_NO_HISTORY warning instead, since there's no limit to
     compare against yet. See CustomerService.simulate_credit for the
     equivalent pure what-if check the frontend can call before this endpoint.
  4. Compute total_amount and total_buying_cost in Python and set them on the
     sale header BEFORE insert — the trigger reads NEW.total_amount and does not
     recompute from items.
  5. Snapshot buying_cost and product_name on each line at sale time.
  6. Set outstanding_amount (= total for ON_DEBT, 0 for CASH); no trigger does this.
  7. Generate the receipt number (RCP-YYYYMMDD-####).
  8. Collect soft warnings (oversell, duplicate products, credit) WITHOUT blocking.

After inserting, the service re-reads the sale so the response reflects the
trigger-updated values (e.g. generated gross_margin).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.business_context import BusinessContext
from app.core.exceptions import NotFoundError, ValidationError
from app.core.idempotency import get_cached_response, store_response
from app.models.product import Product
from app.repositories.sale_repository import SaleRepository
from app.schemas.sale import (
    ReceiptLineOut,
    ReceiptOut,
    SaleCreateRequest,
    SaleCreateResponse,
    SaleItemCreate,
    SaleItemOut,
    SaleListResponse,
    SaleOut,
    SaleWarning,
)

CASH = "CASH"
ON_DEBT = "ON_DEBT"
_IDEMPOTENCY_ENDPOINT = "POST /sales"


class SaleService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = SaleRepository(ctx.session)

    async def create_sale(
        self, data: SaleCreateRequest, *, idempotency_key: str | None = None
    ) -> SaleCreateResponse:
        biz_id = self._ctx.business_id

        cached = await get_cached_response(
            self._ctx.session, biz_id, idempotency_key, _IDEMPOTENCY_ENDPOINT
        )
        if cached is not None:
            return SaleCreateResponse.model_validate(cached)

        # 1. Resolve payment type.
        payment_type = await self._repo.get_payment_type_by_code(data.payment_type)
        if payment_type is None:
            raise ValidationError(
                "Unknown payment_type.", code="invalid_payment_type"
            )

        # 2. For debt sales: customer must exist, be active, and not be
        #    credit-blocked (hard validation — also enforced at the DB level
        #    by trg_validate_sale for the "must have a customer" part). If the
        #    sale would push the customer over their recommended credit
        #    limit, that is a SOFT warning only — per the Customer
        #    Credibility Engine's design, DukaMate never auto-rejects a sale
        #    on affordability grounds; it surfaces the risk and lets the shop
        #    owner (via the frontend) decide.
        customer = None
        if data.payment_type == ON_DEBT:
            if data.customer_id is None:
                raise ValidationError(
                    "A debt sale requires a customer_id.",
                    code="customer_required_for_debt",
                )
            customer = await self._repo.get_customer(biz_id, data.customer_id)
            if customer is None:
                raise ValidationError(
                    "Customer not found for this business.",
                    code="invalid_customer",
                )
            if customer.status != "ACTIVE":
                raise ValidationError(
                    "This customer's profile is archived and cannot receive "
                    "new debt.",
                    code="customer_not_active",
                )
            if customer.is_credit_blocked:
                raise ValidationError(
                    f"{customer.full_name} is credit-blocked and cannot "
                    "receive new debt right now.",
                    code="customer_credit_blocked",
                )

        # 3. Validate products that reference an existing product_id.
        product_ids = [i.product_id for i in data.items if i.product_id is not None]
        products = await self._repo.get_products_by_ids(biz_id, product_ids)
        for pid in product_ids:
            if pid not in products:
                raise ValidationError(
                    "One or more products were not found for this business.",
                    code="invalid_product",
                )

        warnings: list[SaleWarning] = []
        self._collect_duplicate_warnings(data.items, warnings)

        # 4. Compute totals and per-line snapshots (the trigger trusts these).
        total_amount = Decimal("0")
        total_buying_cost = Decimal("0")
        line_plans: list[dict] = []
        # Aggregate requested quantity per product for the oversell check.
        requested_qty: dict[uuid.UUID, Decimal] = {}

        for item in data.items:
            product = products.get(item.product_id) if item.product_id else None
            buying_cost = self._resolve_buying_cost(product)
            name_snapshot = self._resolve_name_snapshot(item, product)

            line_total = (item.quantity * item.unit_price).quantize(Decimal("0.01"))
            line_buy = (
                (item.quantity * buying_cost).quantize(Decimal("0.01"))
                if buying_cost is not None
                else Decimal("0.00")
            )
            total_amount += line_total
            total_buying_cost += line_buy

            if item.product_id is not None:
                requested_qty[item.product_id] = (
                    requested_qty.get(item.product_id, Decimal("0")) + item.quantity
                )

            line_plans.append(
                {
                    "product_id": item.product_id,
                    "product_name_snapshot": name_snapshot,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "buying_cost": buying_cost,
                }
            )

        # Oversell warning: total requested qty for a product exceeds current stock.
        self._collect_oversell_warnings(requested_qty, products, warnings)

        # Credit-limit warning (ON_DEBT only) — soft, never blocking. See the
        # module docstring and CustomerService.simulate_credit, which projects
        # the exact same check ahead of time for the frontend.
        if customer is not None:
            self._collect_credit_limit_warnings(customer, total_amount, warnings)

        # 5. outstanding_amount: full total for debt, zero for cash.
        outstanding = total_amount if data.payment_type == ON_DEBT else Decimal("0.00")

        occurred_at = data.occurred_at or datetime.now(timezone.utc)

        # 6. Receipt number.
        receipt_number = await self._repo.next_receipt_number(biz_id, occurred_at)

        # 7. Insert header (triggers fire on this insert), then items.
        sale = await self._repo.insert_sale(
            business_id=biz_id,
            customer_id=data.customer_id if data.payment_type == ON_DEBT else None,
            payment_type_id=payment_type.id,
            total_amount=total_amount,
            total_buying_cost=total_buying_cost,
            outstanding_amount=outstanding,
            due_date=data.due_date,
            occurred_at=occurred_at,
            note=data.note,
            receipt_number=receipt_number,
        )
        for plan in line_plans:
            await self._repo.insert_sale_item(
                business_id=biz_id,
                sale_id=sale.id,
                product_id=plan["product_id"],
                product_name_snapshot=plan["product_name_snapshot"],
                quantity=plan["quantity"],
                unit_price=plan["unit_price"],
                buying_cost=plan["buying_cost"],
            )

        # Re-read so generated columns (gross_margin, line_total, line_margin)
        # and the eager-loaded items are populated for the response.
        await self._ctx.session.flush()
        await self._ctx.session.refresh(sale)

        response = SaleCreateResponse(
            sale=self._to_sale_out(sale),
            warnings=warnings,
        )
        await store_response(
            self._ctx.session,
            biz_id,
            idempotency_key,
            _IDEMPOTENCY_ENDPOINT,
            response.model_dump(mode="json"),
        )
        return response

    # ----- reads (list / today / by id) -------------------------------------- #
    async def list_sales(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        customer_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        receipt_number: str | None = None,
        search: str | None = None,
        order: str = "recent",
        limit: int = 50,
        offset: int = 0,
    ) -> SaleListResponse:
        biz_id = self._ctx.business_id
        sales = await self._repo.list_sales(
            biz_id,
            start_date=start_date,
            end_date=end_date,
            customer_id=customer_id,
            payment_type_code=payment_type,
            receipt_number=receipt_number,
            search=search,
            order=order,
            limit=limit,
            offset=offset,
        )
        total = await self._repo.count_sales(
            biz_id,
            start_date=start_date,
            end_date=end_date,
            customer_id=customer_id,
            payment_type_code=payment_type,
            receipt_number=receipt_number,
            search=search,
        )
        return SaleListResponse(
            sales=[self._to_sale_out(s) for s in sales],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def list_today_sales(self) -> SaleListResponse:
        """Sales whose occurred_at falls on today's UTC calendar date.

        NOTE: 'today' is computed in UTC. If Dukamate's businesses operate in
        a single local timezone (e.g. EAT, UTC+3), swap datetime.now(timezone.utc)
        for a local-time equivalent so the day boundary matches the shopkeeper's
        actual day.
        """
        today = datetime.now(timezone.utc).date()
        return await self.list_sales(
            start_date=today, end_date=today, limit=1000, offset=0
        )

    async def get_sale(self, sale_id: uuid.UUID) -> SaleOut:
        sale = await self._repo.get_sale_with_items(self._ctx.business_id, sale_id)
        if sale is None:
            raise NotFoundError("Sale not found.", code="sale_not_found")
        return self._to_sale_out(sale)

    async def share_receipt(
        self,
        sale_id: uuid.UUID,
        shared_to: str,
        destination_number: str | None = None,
    ) -> ReceiptOut:
        """Record that a receipt was shared (WhatsApp/print/etc.), to whom,
        and return the rendered receipt plus a WhatsApp click-to-chat link
        when a destination number is supplied.

        Read-only w.r.t. money: does not touch any cached aggregate or
        trigger, only the two receipt-metadata columns on the sale itself.
        """
        sale = await self._repo.get_sale_with_items(self._ctx.business_id, sale_id)
        if sale is None:
            raise NotFoundError("Sale not found.", code="sale_not_found")
        sale = await self._repo.set_receipt_shared(
            sale=sale, shared_to=shared_to, shared_at=datetime.now(timezone.utc)
        )
        await self._ctx.session.refresh(sale)
        return await self._build_receipt(sale, destination_number=destination_number)

    async def preview_receipt(self, sale_id: uuid.UUID) -> ReceiptOut:
        """GET /sales/{id}/receipt — render the receipt without changing
        anything (no share timestamp, no receipt-number change)."""
        sale = await self._repo.get_sale_with_items(self._ctx.business_id, sale_id)
        if sale is None:
            raise NotFoundError("Sale not found.", code="sale_not_found")
        return await self._build_receipt(sale)

    async def regenerate_receipt(self, sale_id: uuid.UUID) -> ReceiptOut:
        """POST /sales/{id}/receipt/regenerate — re-issue the receipt number
        (e.g. the original was lost, blank, or needs reprinting under a fresh
        number) and return the freshly rendered receipt. Does not touch any
        money/aggregate field — only ``receipt_number``.
        """
        sale = await self._repo.get_sale_with_items(self._ctx.business_id, sale_id)
        if sale is None:
            raise NotFoundError("Sale not found.", code="sale_not_found")
        new_number = await self._repo.next_receipt_number(
            self._ctx.business_id, sale.occurred_at
        )
        sale = await self._repo.set_receipt_number(sale=sale, receipt_number=new_number)
        await self._ctx.session.refresh(sale)
        return await self._build_receipt(sale)

    # ----- receipt rendering --------------------------------------------------- #
    async def _build_receipt(
        self, sale, *, destination_number: str | None = None
    ) -> ReceiptOut:
        business = self._ctx.business
        customer_name: str | None = None
        if sale.customer_id is not None:
            customer = await self._repo.get_customer(self._ctx.business_id, sale.customer_id)
            customer_name = customer.full_name if customer else None

        payment_code = "ON_DEBT" if sale.outstanding_amount > 0 or sale.customer_id else "CASH"
        # payment_type_id is the authoritative source; resolve it precisely.
        ptype = await self._repo.get_payment_type_by_id(sale.payment_type_id)
        if ptype is not None:
            payment_code = ptype.code

        footer = self._render_footer(business)

        whatsapp_url = None
        if destination_number:
            whatsapp_url = self._build_whatsapp_url(
                destination_number, business, sale, footer
            )

        return ReceiptOut(
            sale_id=sale.id,
            receipt_number=sale.receipt_number,
            customer_id=sale.customer_id,
            due_date=sale.due_date,
            business_name=business.shop_name,
            business_phone=business.phone_number,
            business_location=business.location,
            currency_symbol=business.currency_symbol or "",
            customer_name=customer_name,
            payment_type=payment_code,
            occurred_at=sale.occurred_at,
            items=[
                ReceiptLineOut(
                    product_name=it.product_name_snapshot,
                    quantity=it.quantity,
                    unit_price=it.unit_price,
                    line_total=(
                        it.line_total
                        if it.line_total is not None
                        else (it.quantity * it.unit_price)
                    ),
                )
                for it in sale.items
            ],
            total_amount=sale.total_amount,
            outstanding_amount=sale.outstanding_amount,
            footer=footer,
            is_correction=sale.is_correction,
            original_sale_id=sale.original_sale_id,
            receipt_shared_at=sale.receipt_shared_at,
            receipt_shared_to=sale.receipt_shared_to,
            whatsapp_share_url=whatsapp_url,
        )

    @staticmethod
    def _render_footer(business) -> str:
        """Build a receipt footer from the business's own profile.

        There is no per-business custom-footer column in the schema, so this
        is a fixed template over fields that already exist (shop name, phone,
        location) plus a thank-you line — rather than a stored, owner-edited
        string. See MIGRATION_NOTES.md for the tradeoff.
        """
        lines = [business.shop_name]
        if business.phone_number:
            lines.append(f"Tel: {business.phone_number}")
        if business.location:
            lines.append(business.location)
        lines.append("Thank you for your business!")
        return "\n".join(lines)

    @staticmethod
    def _build_whatsapp_url(destination_number: str, business, sale, footer: str) -> str:
        """A wa.me click-to-chat link prefilled with a short receipt summary.

        No message is sent server-side — this only builds the deep link; the
        actual send happens in WhatsApp on whichever device opens the URL.
        """
        import urllib.parse

        digits = "".join(ch for ch in destination_number if ch.isdigit())
        text = (
            f"Receipt {sale.receipt_number or sale.id} from {business.shop_name}\n"
            f"Total: {sale.total_amount}\n"
            f"{footer}"
        )
        return f"https://wa.me/{digits}?text={urllib.parse.quote(text)}"

    # ----- helpers ----------------------------------------------------------- #
    @staticmethod
    def _resolve_buying_cost(product: Product | None) -> Decimal | None:
        if product is None:
            return None
        # Prefer the maintained average cost, fall back to buying_price.
        if product.avg_buying_cost is not None:
            return product.avg_buying_cost
        return product.buying_price

    @staticmethod
    def _resolve_name_snapshot(
        item: SaleItemCreate, product: Product | None
    ) -> str:
        # Snapshot the product's current name when linked, else the supplied name.
        if product is not None:
            return product.name
        return item.product_name

    @staticmethod
    def _collect_duplicate_warnings(
        items: list[SaleItemCreate], warnings: list[SaleWarning]
    ) -> None:
        seen: set[uuid.UUID] = set()
        flagged: set[uuid.UUID] = set()
        for item in items:
            if item.product_id is None:
                continue
            if item.product_id in seen and item.product_id not in flagged:
                warnings.append(
                    SaleWarning(
                        code="DUPLICATE_PRODUCT",
                        message=(
                            f"Product appears more than once in this sale: "
                            f"{item.product_name}."
                        ),
                    )
                )
                flagged.add(item.product_id)
            seen.add(item.product_id)

    @staticmethod
    def _collect_oversell_warnings(
        requested_qty: dict[uuid.UUID, Decimal],
        products: dict[uuid.UUID, Product],
        warnings: list[SaleWarning],
    ) -> None:
        for pid, qty in requested_qty.items():
            product = products.get(pid)
            if product is None:
                continue
            if qty > product.stock_on_hand:
                warnings.append(
                    SaleWarning(
                        code="OVERSELL",
                        message=(
                            f"Selling {qty} of '{product.name}' but only "
                            f"{product.stock_on_hand} in stock."
                        ),
                    )
                )

    @staticmethod
    def _collect_credit_limit_warnings(
        customer, total_amount: Decimal, warnings: list[SaleWarning]
    ) -> None:
        """Soft warnings about the customer's credit standing for this debt
        sale. Never raises — the caller (a human, via the frontend) decides
        whether to proceed. Mirrors CustomerService.simulate_credit exactly.
        """
        if customer.credit_level == "UNSCORED":
            warnings.append(
                SaleWarning(
                    code="NEW_CUSTOMER_NO_HISTORY",
                    message=(
                        f"{customer.full_name} has no credit history yet — "
                        "extend debt at your own discretion."
                    ),
                )
            )
            return

        projected_balance = customer.debt_balance + total_amount
        if (
            customer.recommended_credit_limit > 0
            and projected_balance > customer.recommended_credit_limit
        ):
            warnings.append(
                SaleWarning(
                    code="CREDIT_LIMIT_EXCEEDED",
                    message=(
                        f"This sale would bring {customer.full_name}'s debt to "
                        f"{projected_balance}, exceeding their recommended "
                        f"credit limit of {customer.recommended_credit_limit}."
                    ),
                )
            )

    @staticmethod
    def _to_sale_out(sale) -> SaleOut:
        return SaleOut(
            id=sale.id,
            business_id=sale.business_id,
            customer_id=sale.customer_id,
            payment_type_id=sale.payment_type_id,
            total_amount=sale.total_amount,
            total_buying_cost=sale.total_buying_cost,
            gross_margin=sale.gross_margin
            if sale.gross_margin is not None
            else (sale.total_amount - sale.total_buying_cost),
            outstanding_amount=sale.outstanding_amount,
            due_date=sale.due_date,
            occurred_at=sale.occurred_at,
            note=sale.note,
            receipt_number=sale.receipt_number,
            receipt_shared_at=sale.receipt_shared_at,
            receipt_shared_to=sale.receipt_shared_to,
            is_correction=sale.is_correction,
            created_at=sale.created_at,
            items=[
                SaleItemOut(
                    id=it.id,
                    product_id=it.product_id,
                    product_name_snapshot=it.product_name_snapshot,
                    quantity=it.quantity,
                    unit_price=it.unit_price,
                    buying_cost=it.buying_cost,
                    line_total=it.line_total
                    if it.line_total is not None
                    else (it.quantity * it.unit_price),
                    line_margin=it.line_margin
                    if it.line_margin is not None
                    else (
                        it.quantity * it.unit_price
                        - (it.quantity * (it.buying_cost or Decimal("0")))
                    ),
                )
                for it in sale.items
            ],
        )