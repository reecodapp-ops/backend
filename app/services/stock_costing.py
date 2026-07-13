"""Shared weighted-average buying-cost math.

Used by both a real ``StockPurchaseService.create_purchase`` and the
synthetic "Opening stock" purchase ``ProductService.create`` inserts when a
new product is given a starting quantity (Supplier & Product Round spec
§6/§7) -- one formula, one place, so the two call sites can never drift.

``avg_buying_cost`` is a system-only column: it is written ONLY from this
formula, fed by actual purchase records (real or the opening-stock
synthetic one), and is never set directly by an owner edit. This mirrors
``customers.credit_score`` (system-only, computed) vs. the rest of the
customer row (owner-editable) -- see ProductService.update's PATCH
handling and StockPurchaseService's own docstring for the other half of
that separation.
"""
from __future__ import annotations

from decimal import Decimal


def compute_weighted_avg_buying_cost(
    *,
    old_avg: Decimal | None,
    old_buying_price: Decimal | None,
    stock_before: Decimal,
    purchased_qty: Decimal,
    purchased_cost: Decimal,
) -> Decimal | None:
    """New weighted average after adding ``purchased_qty`` units costing
    ``purchased_cost`` in total to a product that had ``stock_before`` units
    on hand (captured BEFORE the stock-incrementing insert -- see the
    caller's own comment on why "before", not a post-insert
    read-and-subtract).

    ``old_avg`` falls back to ``old_buying_price`` when the product has never
    had a purchase recorded yet (fresh product, no system-computed average to
    build on). Returns ``None`` only when there's truly nothing to compute
    from (zero resulting stock and zero cost) -- callers should just skip the
    update in that case.
    """
    new_total_stock = stock_before + purchased_qty
    if new_total_stock <= 0:
        return None
    old_avg_value = old_avg if old_avg is not None else (old_buying_price or Decimal("0"))
    total_value = (old_avg_value * max(stock_before, Decimal("0"))) + purchased_cost
    return (total_value / new_total_stock).quantize(Decimal("0.01"))
