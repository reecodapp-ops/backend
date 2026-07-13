"""Reecod Customer & Supplier Scoring service.

Implements the "Reecod Engine Logic Spec — Customer & Supplier Scoring V1/V2"
document precisely:

  * Data Readiness Gate (§1.1) — never returns a percentage below the
    minimum clean-record threshold; returns ``insufficient_data`` instead.
  * No Fake Defaults (§1.2) — missing factors are dropped and their weight
    redistributed across the remaining valid factors, never defaulted to 50
    or any other placeholder. If everything ends up dropped, the whole score
    is ``insufficient_data``.
  * Score Color Mapping (§2) — the exact four-tier Green/Yellow/Orange/Red
    breakpoints and copy for each of the four scores.
  * Division by zero / no valid denominator anywhere -> ``insufficient_data``
    or that factor is dropped (§7.1).
  * Final score is clamped to [0, 100] (§7.3).

DESIGN DECISION: computed on read, not stored or trigger-maintained
----------------------------------------------------------------------
Unlike the existing Customer Credibility Engine (``customers.credit_score`` /
``credit_level``, maintained by a PostgreSQL trigger after every debt sale/
payment), these four Reecod scores are computed fresh on every request from
``ScoringRepository``'s aggregate queries. This is a deliberate scope
decision for V1:

  * It automatically satisfies spec §7.4 ("edited transactions must trigger
    recalculation") — there is nothing to invalidate, every read is current.
  * It keeps this a pure, portable, additive service (works identically on
    SQLite and PostgreSQL) with zero new triggers/functions to maintain.
  * The tradeoff is read cost: each call runs a handful of aggregate
    queries. For V1 (single-entity profile views, not bulk list filtering)
    this is the right tradeoff — see MIGRATION_NOTES.md for the V2 path
    (materializing into columns + triggers) if bulk sorting/filtering by
    these scores becomes a real requirement.

This module deliberately does NOT touch ``customers.credit_score`` /
``credit_level`` or anything that already consumes them (SaleService's debt
validation, Business Insights, the Customer Credibility Engine's own
endpoints) — Reecod scoring is additive, not a replacement, for V1.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_context import BusinessContext
from app.core.exceptions import NotFoundError
from app.models.customer import Customer
from app.models.supplier import Supplier
from app.repositories.scoring_repository import ScoringRepository
from app.schemas.scoring import (
    CustomerScoresOut,
    CustomerViewMode,
    ScoreOut,
    SupplierScoresOut,
    SupplierViewMode,
)

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

# Activation gates (spec §1.1)
_MIN_CLEAN_CREDIT_TRANSACTIONS = 3
_MIN_PURCHASE_TRANSACTIONS = 3
_MIN_CLEAN_SUPPLIER_ORDERS = 3
_MIN_PRICED_SUPPLIER_ORDERS = 3

# Severity denominators (spec §3.1, §4.1)
_DELAY_SEVERITY_WINDOW_DAYS = Decimal("30")
_DELIVERY_DELAY_WINDOW_DAYS = Decimal("14")
_CREDIT_TERMS_TARGET_DAYS = Decimal("14")

_PAYMENT_FLEXIBILITY_SCORES = {
    "FLEXIBLE": Decimal("100"),
    "SOMETIMES_FLEXIBLE": Decimal("60"),
    "STRICT_CASH": Decimal("20"),
}

# GET /suppliers's `reliability_level` filter uses short UPPER_SNAKE keys,
# distinct from the display labels used everywhere else (spec §2.3 tiers).
_RELIABILITY_LABEL_TO_LEVEL_KEY = {
    "Reliable": "RELIABLE",
    "Fair": "FAIR",
    "Unstable": "UNSTABLE",
    "Avoid for Urgent Stock": "AVOID_URGENT",
}


class _Insufficient(Exception):
    """Internal signal: fall through to an insufficient_data ScoreOut."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _clamp(value: Decimal) -> int:
    return int(max(_ZERO, min(_HUNDRED, value)).to_integral_value(rounding="ROUND_HALF_UP"))


def _weighted_average(factors: list[tuple[str, Decimal, Decimal | None]]) -> tuple[Decimal, dict]:
    """Redistribute weight across valid (non-None) factors and combine.

    ``factors`` is a list of (name, weight, value) where value is ``None``
    when that factor couldn't be computed. Raises ``_Insufficient`` if no
    factor survives (§1.2's "too many factors missing" case).
    """
    valid = [(name, weight, value) for name, weight, value in factors if value is not None]
    if not valid:
        raise _Insufficient("No scoring factors could be computed from the available data.")
    total_weight = sum(w for _, w, _ in valid)
    score = sum((value * weight) for _, weight, value in valid) / total_weight
    breakdown = {name: float(value) for name, _, value in valid}
    return score, breakdown


class ScoringService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._session = ctx.session
        self._repo = ScoringRepository(ctx.session)

    # ========================================================================
    # Customers: Credit Trust + Buyer Value
    # ========================================================================
    async def get_customer_scores(
        self,
        customer_id: uuid.UUID,
        *,
        view_mode: CustomerViewMode = "trust",
    ) -> CustomerScoresOut:
        business_id = self._ctx.business_id
        customer = await self._session.get(Customer, customer_id)
        if customer is None or customer.business_id != business_id:
            raise NotFoundError("Customer not found.", code="customer_not_found")

        trust = await self._credit_trust_score(customer_id)
        value = await self._buyer_value_score(business_id, customer_id)

        return CustomerScoresOut(
            customer_id=customer.id,
            name=customer.full_name,
            active_view=view_mode,
            scores={"trust": trust, "value": value},
        )

    async def _credit_trust_score(self, customer_id: uuid.UUID) -> ScoreOut:
        inputs = await self._repo.credit_trust_inputs(customer_id)

        if inputs["clean_count"] < _MIN_CLEAN_CREDIT_TRANSACTIONS:
            return ScoreOut(
                status="insufficient_data",
                label="New",
                meaning="Not enough clean credit records yet to calculate a trust score.",
                action="Record due dates on credit sales to start building this score.",
                reason=f"Needs {_MIN_CLEAN_CREDIT_TRANSACTIONS} credit records to calculate.",
            )

        # Payment Punctuality (40%) — needs at least one closed (PAID) clean
        # transaction to mean anything.
        punctuality = None
        if inputs["clean_paid_count"] > 0:
            punctuality = (
                Decimal(inputs["on_time_count"]) / Decimal(inputs["clean_paid_count"])
            ) * _HUNDRED

        # Debt Clearance (30%)
        clearance = None
        if inputs["total_owed"] > 0:
            clearance = min(
                (inputs["total_paid"] / inputs["total_owed"]) * _HUNDRED, _HUNDRED
            )

        # Delay Severity (20%) — no late payments at all => perfect 100.
        delay_severity = _HUNDRED - min(
            (inputs["avg_days_late"] / _DELAY_SEVERITY_WINDOW_DAYS) * _HUNDRED, _HUNDRED
        )

        # Current Unpaid Risk (10%)
        unpaid_risk = None
        if inputs["total_owed"] > 0:
            unpaid_risk = _HUNDRED - min(
                (inputs["current_unpaid"] / inputs["total_owed"]) * _HUNDRED, _HUNDRED
            )

        try:
            raw_score, breakdown = _weighted_average(
                [
                    ("payment_punctuality", Decimal("0.40"), punctuality),
                    ("debt_clearance", Decimal("0.30"), clearance),
                    ("delay_severity", Decimal("0.20"), delay_severity),
                    ("current_unpaid_risk", Decimal("0.10"), unpaid_risk),
                ]
            )
        except _Insufficient:
            return ScoreOut(
                status="insufficient_data",
                label="Needs More Data",
                meaning="The recorded credit transactions don't have enough detail to score.",
                action="Record due dates and payment dates to enable this score.",
                reason="Needs more complete credit records to calculate.",
            )

        percentage = _clamp(raw_score)
        return self._credit_trust_tier(percentage, breakdown)

    @staticmethod
    def _credit_trust_tier(percentage: int, breakdown: dict) -> ScoreOut:
        if percentage >= 75:
            color, label = "green", "Trusted"
            meaning = "This customer usually pays and has low unpaid risk."
            action = "Safe to offer normal credit."
        elif percentage >= 50:
            color, label = "yellow", "Watch Carefully"
            meaning = "This customer can receive limited credit with attention."
            action = "Offer limited credit only. Watch payment behavior."
        elif percentage >= 25:
            color, label = "orange", "Limit Credit"
            meaning = "This customer is risky — recover the old balance before giving more."
            action = "Do not increase credit. Recover old balance first."
        else:
            color, label = "red", "Cash Only"
            meaning = "This customer is high risk for credit right now."
            action = "Cash only. No credit until debt behavior improves."
        return ScoreOut(
            percentage=percentage, color=color, label=label,
            status="calculated", meaning=meaning, action=action, factors=breakdown,
        )

    async def _buyer_value_score(
        self, business_id: uuid.UUID, customer_id: uuid.UUID
    ) -> ScoreOut:
        inputs = await self._repo.buyer_value_inputs(business_id, customer_id)

        if inputs["customer_purchase_count"] < _MIN_PURCHASE_TRANSACTIONS:
            return ScoreOut(
                status="insufficient_data",
                label="New",
                meaning="Not enough purchase history yet to calculate buyer value.",
                action="No action yet — this customer is still new.",
                reason=f"Needs {_MIN_PURCHASE_TRANSACTIONS} purchases to calculate.",
            )

        distinct_customers = inputs["shop_distinct_customers"] or 0
        if distinct_customers == 0:
            return ScoreOut(
                status="insufficient_data",
                label="Needs More Data",
                meaning="Not enough shop-wide purchase history to compare against yet.",
                action="No action yet.",
                reason="Needs more shop purchase history to calculate.",
            )

        avg_purchase_count = Decimal(inputs["shop_purchase_count"]) / Decimal(distinct_customers)
        avg_spend = inputs["shop_total_spend"] / Decimal(distinct_customers)

        frequency = None
        if avg_purchase_count > 0:
            frequency = min(
                (Decimal(inputs["customer_purchase_count"]) / avg_purchase_count) * _HUNDRED,
                _HUNDRED,
            )
        volume = None
        if avg_spend > 0:
            volume = min((inputs["customer_total_spend"] / avg_spend) * _HUNDRED, _HUNDRED)

        try:
            raw_score, breakdown = _weighted_average(
                [
                    ("purchase_frequency", Decimal("0.50"), frequency),
                    ("purchase_volume", Decimal("0.50"), volume),
                ]
            )
        except _Insufficient:
            return ScoreOut(
                status="insufficient_data",
                label="Needs More Data",
                meaning="Not enough shop-wide activity to compare this customer against yet.",
                action="No action yet.",
                reason="Needs more shop purchase history to calculate.",
            )

        percentage = _clamp(raw_score)
        return self._buyer_value_tier(percentage, breakdown)

    @staticmethod
    def _buyer_value_tier(percentage: int, breakdown: dict) -> ScoreOut:
        if percentage >= 75:
            color, label = "green", "Top Buyer"
            meaning = "This customer buys more than the average customer."
            action = "Consider loyalty treatment or priority service."
        elif percentage >= 50:
            color, label = "yellow", "Good Buyer"
            meaning = "A valuable customer with steady activity."
            action = "Maintain the relationship. Encourage repeat buying."
        elif percentage >= 25:
            color, label = "orange", "Occasional Buyer"
            meaning = "Some value, but not a major buyer yet."
            action = "No special treatment needed yet."
        else:
            color, label = "red", "Low Activity"
            meaning = "A rare or low-value buyer so far."
            action = "No action unless strategically important."
        return ScoreOut(
            percentage=percentage, color=color, label=label,
            status="calculated", meaning=meaning, action=action, factors=breakdown,
        )

    # ========================================================================
    # Suppliers: Reliability + Terms
    # ========================================================================
    async def get_supplier_scores(
        self,
        supplier_id: uuid.UUID,
        *,
        view_mode: SupplierViewMode = "reliability",
    ) -> SupplierScoresOut:
        business_id = self._ctx.business_id
        supplier = await self._session.get(Supplier, supplier_id)
        if supplier is None or supplier.business_id != business_id:
            raise NotFoundError("Supplier not found.", code="supplier_not_found")

        reliability = await self._supplier_reliability_score(supplier_id)
        terms = await self._supplier_terms_score(business_id, supplier)

        return SupplierScoresOut(
            supplier_id=supplier.id,
            name=supplier.name,
            active_view=view_mode,
            scores={"reliability": reliability, "terms": terms},
        )

    async def reliability_badges_bulk(
        self, supplier_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, ScoreOut]:
        """The batched ("no N+1") reliability computation behind
        ``GET /suppliers``'s per-row ``reliability_label``/``reliability_color``
        and the ``reliability_level`` filter / ``order=reliability_desc`` sort.
        One repository round trip (itself a fixed 3 queries, see
        ``ScoringRepository.supplier_reliability_inputs_bulk``) regardless of
        how many suppliers are passed in.
        """
        if not supplier_ids:
            return {}
        inputs_by_supplier = await self._repo.supplier_reliability_inputs_bulk(supplier_ids)
        return {
            sid: self._reliability_score_from_inputs(inputs)
            for sid, inputs in inputs_by_supplier.items()
        }

    async def terms_scores_bulk(
        self, business_id: uuid.UUID, supplier_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, ScoreOut]:
        """Batched Terms Score computation, used only when
        ``order=terms_desc`` is requested on ``GET /suppliers`` (Terms isn't
        a displayed list field, only a sort key) -- fixed 4 queries
        regardless of supplier/item count, see
        ``ScoringRepository.supplier_terms_inputs_bulk``.
        """
        if not supplier_ids:
            return {}
        inputs_by_supplier = await self._repo.supplier_terms_inputs_bulk(
            business_id, supplier_ids
        )
        return {
            sid: self._terms_score_from_inputs(inputs)
            for sid, inputs in inputs_by_supplier.items()
        }

    @staticmethod
    def reliability_badge(score: ScoreOut) -> tuple[str | None, str]:
        """(label, color) for a GET /suppliers list row. Deliberately
        different from the raw ``ScoreOut`` contract used by
        ``/suppliers/{id}/scores`` (where insufficient_data means
        ``color=None``): the list-row badge contract wants an explicit
        "gray" color with a null label so the frontend can render a
        "Needs more records" chip without a special case for None.
        """
        if score.status == "insufficient_data":
            return None, "gray"
        return score.label, score.color

    @staticmethod
    def reliability_level_key(score: ScoreOut) -> str | None:
        """Map a computed reliability ScoreOut to the ``reliability_level``
        filter's enum (RELIABLE/FAIR/UNSTABLE/AVOID_URGENT), or ``None`` if
        the supplier hasn't cleared the data-readiness gate yet (such
        suppliers never match any ``reliability_level`` filter value --
        there's nothing to filter them into)."""
        return _RELIABILITY_LABEL_TO_LEVEL_KEY.get(score.label)

    async def _supplier_reliability_score(self, supplier_id: uuid.UUID) -> ScoreOut:
        inputs = await self._repo.supplier_reliability_inputs(supplier_id)
        return self._reliability_score_from_inputs(inputs)

    @classmethod
    def _reliability_score_from_inputs(cls, inputs: dict) -> ScoreOut:
        """Pure (no I/O) reliability computation from an already-fetched
        inputs dict — shared by the single-supplier path
        (``_supplier_reliability_score``) and the batched list-view path
        (``reliability_badges_bulk``), so the two can never drift apart.
        """
        if inputs["clean_count"] < _MIN_CLEAN_SUPPLIER_ORDERS:
            return ScoreOut(
                status="insufficient_data",
                label="New",
                meaning="Not enough delivery records yet to calculate reliability.",
                action="Record promised and actual delivery dates on orders from this supplier.",
                reason=f"Needs {_MIN_CLEAN_SUPPLIER_ORDERS} supplier orders to calculate.",
            )

        punctuality = None
        if inputs["delivered_count"] > 0:
            punctuality = (
                Decimal(inputs["on_time_delivered_count"]) / Decimal(inputs["delivered_count"])
            ) * _HUNDRED

        fill_rate = None
        if inputs["total_ordered"] > 0:
            fill_rate = min(
                (inputs["total_received"] / inputs["total_ordered"]) * _HUNDRED, _HUNDRED
            )

        delay_severity = _HUNDRED - min(
            (inputs["avg_days_late"] / _DELIVERY_DELAY_WINDOW_DAYS) * _HUNDRED, _HUNDRED
        )

        try:
            raw_score, breakdown = _weighted_average(
                [
                    ("delivery_punctuality", Decimal("0.40"), punctuality),
                    ("order_fill_rate", Decimal("0.40"), fill_rate),
                    ("delivery_delay_severity", Decimal("0.20"), delay_severity),
                ]
            )
        except _Insufficient:
            return ScoreOut(
                status="insufficient_data",
                label="Needs More Data",
                meaning="The recorded orders don't have enough delivery detail to score.",
                action="Record promised/actual delivery dates and items ordered/received.",
                reason="Needs more complete delivery records to calculate.",
            )

        percentage = _clamp(raw_score)
        return cls._supplier_reliability_tier(percentage, breakdown)

    @staticmethod
    def _supplier_reliability_tier(percentage: int, breakdown: dict) -> ScoreOut:
        if percentage >= 75:
            color, label = "green", "Reliable"
            meaning = "This supplier can be prioritized for restocking."
            action = "Prioritize for restocking."
        elif percentage >= 50:
            color, label = "yellow", "Fair"
            meaning = "This supplier usually delivers, but not always consistently."
            action = "Usable, but compare before depending fully."
        elif percentage >= 25:
            color, label = "orange", "Unstable"
            meaning = "Risky for urgent or important stock."
            action = "Avoid for urgent or important stock."
        else:
            color, label = "red", "Avoid for Urgent Stock"
            meaning = "This supplier can damage stock availability."
            action = "Do not rely on this supplier for critical stock."
        return ScoreOut(
            percentage=percentage, color=color, label=label,
            status="calculated", meaning=meaning, action=action, factors=breakdown,
        )

    async def _supplier_terms_score(
        self, business_id: uuid.UUID, supplier: Supplier
    ) -> ScoreOut:
        inputs = await self._repo.supplier_terms_inputs(business_id, supplier.id)
        return self._terms_score_from_inputs(inputs)

    @classmethod
    def _terms_score_from_inputs(cls, inputs: dict) -> ScoreOut:
        """Pure (no I/O) terms computation from an already-fetched inputs
        dict — shared by the single-supplier path (``_supplier_terms_score``)
        and the batched ``order=terms_desc`` sort path.
        """
        if inputs["priced_order_count"] < _MIN_PRICED_SUPPLIER_ORDERS:
            return ScoreOut(
                status="insufficient_data",
                label="New",
                meaning="Not enough pricing records yet to calculate terms.",
                action="Record buying costs on orders from this supplier.",
                reason="Needs more pricing or payment records.",
            )

        price_fairness = None
        if inputs["price_ratios"]:
            price_fairness = sum(inputs["price_ratios"]) / Decimal(len(inputs["price_ratios"]))

        credit_terms = None
        if inputs["credit_terms_days"] is not None:
            credit_terms = min(
                (Decimal(inputs["credit_terms_days"]) / _CREDIT_TERMS_TARGET_DAYS) * _HUNDRED,
                _HUNDRED,
            )

        payment_flexibility = _PAYMENT_FLEXIBILITY_SCORES.get(
            inputs["payment_flexibility"] or ""
        )

        try:
            raw_score, breakdown = _weighted_average(
                [
                    ("price_fairness", Decimal("0.50"), price_fairness),
                    ("credit_terms", Decimal("0.30"), credit_terms),
                    ("payment_flexibility", Decimal("0.20"), payment_flexibility),
                ]
            )
        except _Insufficient:
            return ScoreOut(
                status="insufficient_data",
                label="Needs More Data",
                meaning="Not enough pricing or terms detail recorded to score this supplier.",
                action="Record credit terms, payment flexibility, or buying costs.",
                reason="Needs more pricing or payment records.",
            )

        percentage = _clamp(raw_score)
        return cls._supplier_terms_tier(percentage, breakdown)

    @staticmethod
    def _supplier_terms_tier(percentage: int, breakdown: dict) -> ScoreOut:
        if percentage >= 75:
            color, label = "green", "Strong Terms"
            meaning = "This supplier offers good pricing or flexible payment terms."
            action = "Consider prioritizing for planned orders."
        elif percentage >= 50:
            color, label = "yellow", "Fair Terms"
            meaning = "Acceptable terms, but not exceptional."
            action = "Compare when buying large stock."
        elif percentage >= 25:
            color, label = "orange", "Weak Terms"
            meaning = "May hurt cash flow or margins."
            action = "Use only when necessary."
        else:
            color, label = "red", "Poor Terms"
            meaning = "Poor pricing or inflexible terms."
            action = "Avoid unless no alternative exists."
        return ScoreOut(
            percentage=percentage, color=color, label=label,
            status="calculated", meaning=meaning, action=action, factors=breakdown,
        )
