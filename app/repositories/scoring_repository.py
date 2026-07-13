"""Reecod Scoring repository.

Every method here is a single aggregate ``SELECT`` (no N+1, no Python-side
loops over raw rows) returning the raw counts/sums the service layer needs to
apply the Reecod Engine Logic Spec's formulas. This repository does NOT
compute percentages, gates, or labels — that is ``ScoringService``'s job,
exactly mirroring the InsightsRepository/InsightsService split already used
elsewhere in this codebase.

"CLEAN" TRANSACTION DEFINITIONS (spec §1.1)
---------------------------------------------
A clean **credit transaction** (customer_debt_ledger row) is one that has a
``due_date`` recorded — every other required field (customer, amount owed,
date given, payment status, payment date if paid) is already guaranteed
non-null by the schema itself, so ``due_date IS NOT NULL`` is the only extra
condition needed to match the spec's definition.

A clean **purchase transaction** (a sale) is one linked to a customer
(``customer_id IS NOT NULL``). Note: in this codebase, CASH sales are never
attributed to a customer even if one was selected at checkout (see
``SaleService.create_sale``) — only ON_DEBT sales carry ``customer_id``. This
means Buyer Value is, in practice, computed from a customer's debt-sale
history today. This is a known scope limit, not a bug — see
MIGRATION_NOTES.md.

A clean **supplier order** (stock_purchases row) is one with a
``promised_delivery_date`` recorded — the field the schema doesn't otherwise
require.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.sale import CustomerDebtLedger, Sale
from app.models.stock_purchase import StockPurchase, StockPurchaseItem
from app.models.supplier import Supplier

_ZERO = Decimal("0")


class ScoringRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ========================================================================
    # Customer Credit Trust (spec §3.1)
    # ========================================================================
    async def credit_trust_inputs(self, customer_id: uuid.UUID) -> dict:
        """One round trip for every Credit Trust factor input."""
        clean = CustomerDebtLedger.due_date.is_not(None)
        stmt = select(
            func.count().filter(clean).label("clean_count"),
            func.count().filter(clean, CustomerDebtLedger.status == "PAID").label("clean_paid_count"),
            func.count()
            .filter(
                clean,
                CustomerDebtLedger.status == "PAID",
                func.date(CustomerDebtLedger.fully_paid_at) <= CustomerDebtLedger.due_date,
            )
            .label("on_time_count"),
            func.count()
            .filter(
                clean,
                CustomerDebtLedger.status == "PAID",
                func.date(CustomerDebtLedger.fully_paid_at) > CustomerDebtLedger.due_date,
            )
            .label("late_count"),
            func.coalesce(func.sum(CustomerDebtLedger.paid_amount).filter(clean), 0).label("total_paid"),
            func.coalesce(func.sum(CustomerDebtLedger.original_amount).filter(clean), 0).label("total_owed"),
            func.coalesce(
                func.sum(CustomerDebtLedger.remaining_amount).filter(
                    clean, CustomerDebtLedger.status.in_(["UNPAID", "PARTIAL"])
                ),
                0,
            ).label("current_unpaid"),
        ).where(CustomerDebtLedger.customer_id == customer_id)

        row = (await self._session.execute(stmt)).mappings().one()

        # Average days late computed portably in Python from a lightweight
        # per-row fetch instead of relying on a dialect-specific date-diff
        # function in the aggregate above (julianday() is SQLite-only;
        # PostgreSQL needs a different expression) — see _avg_days_late.
        avg_days_late = await self._avg_days_late(customer_id)

        return {
            "clean_count": row["clean_count"],
            "clean_paid_count": row["clean_paid_count"],
            "on_time_count": row["on_time_count"],
            "late_count": row["late_count"],
            "avg_days_late": avg_days_late,
            "total_paid": Decimal(str(row["total_paid"])),
            "total_owed": Decimal(str(row["total_owed"])),
            "current_unpaid": Decimal(str(row["current_unpaid"])),
        }

    async def _avg_days_late(self, customer_id: uuid.UUID) -> Decimal:
        """Average (fully_paid_at.date() - due_date) in days, over clean, PAID,
        late transactions only. Fetches just the two date columns for the
        (small, bounded) late subset — portable across SQLite/PostgreSQL
        without a dialect-specific date-diff SQL function.
        """
        stmt = select(
            CustomerDebtLedger.due_date, CustomerDebtLedger.fully_paid_at
        ).where(
            CustomerDebtLedger.customer_id == customer_id,
            CustomerDebtLedger.due_date.is_not(None),
            CustomerDebtLedger.status == "PAID",
            CustomerDebtLedger.fully_paid_at.is_not(None),
        )
        rows = (await self._session.execute(stmt)).all()
        late_days = []
        for due_date, fully_paid_at in rows:
            paid_date = fully_paid_at.date() if hasattr(fully_paid_at, "date") else fully_paid_at
            delta = (paid_date - due_date).days
            if delta > 0:
                late_days.append(delta)
        if not late_days:
            return _ZERO
        return Decimal(sum(late_days)) / Decimal(len(late_days))

    # ========================================================================
    # Buyer Value (spec §3.2)
    # ========================================================================
    async def buyer_value_inputs(self, business_id: uuid.UUID, customer_id: uuid.UUID) -> dict:
        """Customer's own purchase count/spend, plus the shop-wide average
        per customer (among customers with at least one purchase — see
        module docstring for what counts as a "purchase transaction" here).
        """
        customer_stmt = select(
            func.count(Sale.id), func.coalesce(func.sum(Sale.total_amount), 0)
        ).where(
            Sale.business_id == business_id,
            Sale.customer_id == customer_id,
            Sale.is_correction.is_(False),
        )
        cust_count, cust_spend = (await self._session.execute(customer_stmt)).one()

        shop_stmt = select(
            func.count(Sale.id),
            func.coalesce(func.sum(Sale.total_amount), 0),
            func.count(func.distinct(Sale.customer_id)),
        ).where(
            Sale.business_id == business_id,
            Sale.customer_id.is_not(None),
            Sale.is_correction.is_(False),
        )
        shop_count, shop_spend, distinct_customers = (
            await self._session.execute(shop_stmt)
        ).one()

        return {
            "customer_purchase_count": cust_count,
            "customer_total_spend": Decimal(str(cust_spend)),
            "shop_purchase_count": shop_count,
            "shop_total_spend": Decimal(str(shop_spend)),
            "shop_distinct_customers": distinct_customers,
        }

    # ========================================================================
    # Supplier Reliability (spec §4.1)
    # ========================================================================
    async def supplier_reliability_inputs(self, supplier_id: uuid.UUID) -> dict:
        clean = StockPurchase.promised_delivery_date.is_not(None)
        delivered = clean & StockPurchase.actual_delivery_date.is_not(None)

        header_stmt = select(
            func.count().filter(clean).label("clean_count"),
            func.count().filter(delivered).label("delivered_count"),
            func.count()
            .filter(
                delivered,
                StockPurchase.actual_delivery_date <= StockPurchase.promised_delivery_date,
            )
            .label("on_time_delivered_count"),
        ).where(
            StockPurchase.supplier_id == supplier_id,
            StockPurchase.is_correction.is_(False),
        )
        row = (await self._session.execute(header_stmt)).mappings().one()

        late_stmt = select(
            StockPurchase.promised_delivery_date, StockPurchase.actual_delivery_date
        ).where(
            StockPurchase.supplier_id == supplier_id,
            StockPurchase.is_correction.is_(False),
            StockPurchase.promised_delivery_date.is_not(None),
            StockPurchase.actual_delivery_date.is_not(None),
            StockPurchase.actual_delivery_date > StockPurchase.promised_delivery_date,
        )
        late_rows = (await self._session.execute(late_stmt)).all()
        late_days = [(actual - promised).days for promised, actual in late_rows]
        avg_days_late = (
            Decimal(sum(late_days)) / Decimal(len(late_days)) if late_days else _ZERO
        )

        fill_stmt = (
            select(
                func.coalesce(func.sum(StockPurchaseItem.quantity), 0),
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            StockPurchaseItem.quantity_ordered, StockPurchaseItem.quantity
                        )
                    ),
                    0,
                ),
            )
            .select_from(StockPurchaseItem)
            .join(StockPurchase, StockPurchase.id == StockPurchaseItem.purchase_id)
            .where(
                StockPurchase.supplier_id == supplier_id,
                StockPurchase.is_correction.is_(False),
            )
        )
        received, ordered = (await self._session.execute(fill_stmt)).one()

        return {
            "clean_count": row["clean_count"],
            "delivered_count": row["delivered_count"],
            "on_time_delivered_count": row["on_time_delivered_count"],
            "avg_days_late": avg_days_late,
            "total_received": Decimal(str(received)),
            "total_ordered": Decimal(str(ordered)),
        }

    async def supplier_reliability_inputs_bulk(
        self, supplier_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict]:
        """Same inputs as ``supplier_reliability_inputs``, for every supplier
        in ``supplier_ids`` at once -- exactly three queries total (one
        header aggregate, one late-delivery detail fetch, one fill-rate
        aggregate), each grouped by ``supplier_id``, regardless of how many
        suppliers are requested. This is what makes
        ``GET /suppliers``'s per-row ``reliability_label``/``reliability_color``
        safe to compute without an N+1 query per supplier.
        """
        if not supplier_ids:
            return {}

        clean = StockPurchase.promised_delivery_date.is_not(None)
        delivered = clean & StockPurchase.actual_delivery_date.is_not(None)

        header_stmt = (
            select(
                StockPurchase.supplier_id,
                func.count().filter(clean).label("clean_count"),
                func.count().filter(delivered).label("delivered_count"),
                func.count()
                .filter(
                    delivered,
                    StockPurchase.actual_delivery_date <= StockPurchase.promised_delivery_date,
                )
                .label("on_time_delivered_count"),
            )
            .where(
                StockPurchase.supplier_id.in_(supplier_ids),
                StockPurchase.is_correction.is_(False),
            )
            .group_by(StockPurchase.supplier_id)
        )
        header_rows = (await self._session.execute(header_stmt)).mappings().all()
        by_supplier: dict[uuid.UUID, dict] = {
            sid: {
                "clean_count": 0, "delivered_count": 0, "on_time_delivered_count": 0,
                "avg_days_late": _ZERO, "total_received": _ZERO, "total_ordered": _ZERO,
            }
            for sid in supplier_ids
        }
        for row in header_rows:
            by_supplier[row["supplier_id"]].update(
                clean_count=row["clean_count"],
                delivered_count=row["delivered_count"],
                on_time_delivered_count=row["on_time_delivered_count"],
            )

        late_stmt = select(
            StockPurchase.supplier_id,
            StockPurchase.promised_delivery_date,
            StockPurchase.actual_delivery_date,
        ).where(
            StockPurchase.supplier_id.in_(supplier_ids),
            StockPurchase.is_correction.is_(False),
            StockPurchase.promised_delivery_date.is_not(None),
            StockPurchase.actual_delivery_date.is_not(None),
            StockPurchase.actual_delivery_date > StockPurchase.promised_delivery_date,
        )
        late_rows = (await self._session.execute(late_stmt)).all()
        late_days_by_supplier: dict[uuid.UUID, list[int]] = {}
        for supplier_id, promised, actual in late_rows:
            late_days_by_supplier.setdefault(supplier_id, []).append((actual - promised).days)
        for sid, days in late_days_by_supplier.items():
            by_supplier[sid]["avg_days_late"] = Decimal(sum(days)) / Decimal(len(days))

        fill_stmt = (
            select(
                StockPurchase.supplier_id,
                func.coalesce(func.sum(StockPurchaseItem.quantity), 0),
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            StockPurchaseItem.quantity_ordered, StockPurchaseItem.quantity
                        )
                    ),
                    0,
                ),
            )
            .select_from(StockPurchaseItem)
            .join(StockPurchase, StockPurchase.id == StockPurchaseItem.purchase_id)
            .where(
                StockPurchase.supplier_id.in_(supplier_ids),
                StockPurchase.is_correction.is_(False),
            )
            .group_by(StockPurchase.supplier_id)
        )
        fill_rows = (await self._session.execute(fill_stmt)).all()
        for supplier_id, received, ordered in fill_rows:
            by_supplier[supplier_id]["total_received"] = Decimal(str(received))
            by_supplier[supplier_id]["total_ordered"] = Decimal(str(ordered))

        return by_supplier

    # ========================================================================
    # Supplier Terms (spec §4.2)
    # ========================================================================
    async def supplier_terms_inputs(
        self, business_id: uuid.UUID, supplier_id: uuid.UUID, *, recent_days: int = 180
    ) -> dict:
        result = await self.supplier_terms_inputs_bulk(
            business_id, [supplier_id], recent_days=recent_days
        )
        return result[supplier_id]

    async def supplier_terms_inputs_bulk(
        self,
        business_id: uuid.UUID,
        supplier_ids: list[uuid.UUID],
        *,
        recent_days: int = 180,
    ) -> dict[uuid.UUID, dict]:
        """Same inputs as the single-supplier version, for every supplier in
        ``supplier_ids`` at once. Four queries total (priced-order counts,
        supplier rows, per-supplier priced items, and product benchmarks),
        regardless of how many suppliers or items are involved — replaces
        what used to be one query per line item per supplier (a real N+1
        the single-supplier method had internally before this).
        """
        empty = {
            "priced_order_count": 0, "price_ratios": [],
            "credit_terms_days": None, "payment_flexibility": None,
        }
        if not supplier_ids:
            return {}

        cutoff = date.today() - timedelta(days=recent_days)

        priced_orders_stmt = (
            select(
                StockPurchase.supplier_id,
                func.count(func.distinct(StockPurchase.id)),
            )
            .select_from(StockPurchase)
            .join(StockPurchaseItem, StockPurchaseItem.purchase_id == StockPurchase.id)
            .where(
                StockPurchase.supplier_id.in_(supplier_ids),
                StockPurchase.is_correction.is_(False),
                StockPurchaseItem.unit_buying_cost.is_not(None),
            )
            .group_by(StockPurchase.supplier_id)
        )
        priced_order_counts = dict(
            (await self._session.execute(priced_orders_stmt)).all()
        )

        suppliers_stmt = select(Supplier).where(Supplier.id.in_(supplier_ids))
        suppliers_by_id = {
            s.id: s for s in (await self._session.execute(suppliers_stmt)).scalars().all()
        }

        # Per-item (supplier, product, unit_buying_cost) for every supplier's
        # recent priced purchases, in one query.
        supplier_items_stmt = (
            select(
                StockPurchase.supplier_id,
                StockPurchaseItem.product_id,
                StockPurchaseItem.unit_buying_cost,
            )
            .select_from(StockPurchaseItem)
            .join(StockPurchase, StockPurchase.id == StockPurchaseItem.purchase_id)
            .where(
                StockPurchase.supplier_id.in_(supplier_ids),
                StockPurchase.is_correction.is_(False),
                StockPurchaseItem.unit_buying_cost.is_not(None),
                StockPurchaseItem.product_id.is_not(None),
                func.date(StockPurchase.occurred_at) >= cutoff,
            )
        )
        supplier_items = (await self._session.execute(supplier_items_stmt)).all()

        product_ids = {product_id for _, product_id, _ in supplier_items}
        benchmarks: dict[uuid.UUID, Decimal] = {}
        if product_ids:
            benchmark_stmt = (
                select(
                    StockPurchaseItem.product_id,
                    func.avg(StockPurchaseItem.unit_buying_cost),
                )
                .select_from(StockPurchaseItem)
                .join(StockPurchase, StockPurchase.id == StockPurchaseItem.purchase_id)
                .where(
                    StockPurchaseItem.product_id.in_(product_ids),
                    StockPurchaseItem.unit_buying_cost.is_not(None),
                    StockPurchase.is_correction.is_(False),
                    func.date(StockPurchase.occurred_at) >= cutoff,
                )
                .group_by(StockPurchaseItem.product_id)
            )
            for product_id, avg_cost in (await self._session.execute(benchmark_stmt)).all():
                if avg_cost is not None:
                    benchmarks[product_id] = Decimal(str(avg_cost))

        price_ratios_by_supplier: dict[uuid.UUID, list[Decimal]] = {
            sid: [] for sid in supplier_ids
        }
        for supplier_id, product_id, supplier_price in supplier_items:
            if not supplier_price or supplier_price <= 0:
                continue
            benchmark = benchmarks.get(product_id)
            if benchmark is None:
                continue
            ratio = min((benchmark / Decimal(str(supplier_price))) * 100, Decimal("100"))
            price_ratios_by_supplier[supplier_id].append(ratio)

        result: dict[uuid.UUID, dict] = {}
        for sid in supplier_ids:
            supplier = suppliers_by_id.get(sid)
            result[sid] = {
                "priced_order_count": priced_order_counts.get(sid, 0),
                "price_ratios": price_ratios_by_supplier.get(sid, []),
                "credit_terms_days": supplier.credit_terms_days if supplier else None,
                "payment_flexibility": supplier.payment_flexibility if supplier else None,
            }
        return result
