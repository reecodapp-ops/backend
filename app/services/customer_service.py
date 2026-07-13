"""Customer service — profile, credit intelligence, ledger, payment history.

This module is the "brain" behind DukaMate's flagship feature: helping a shop
owner decide whether to give a customer more debt. It is deliberately a pure
*presentation and decision-support* layer over data that is already computed
and persisted elsewhere:

  * The credit SCORE itself (credit_score/credit_level/is_credit_blocked/
    recommended_credit_limit/average_payment_days/late_payment_count/
    on_time_payment_count/fully_paid_debt_count) is written exclusively by the
    database function ``recompute_customer_credit_score()`` -- fired by
    triggers after every debt sale and every payment, and nightly by
    ``app/workers/credit_engine.py``. This service NEVER assigns to those
    columns.
  * The Decision Engine issues (CUSTOMER_HIGH_RISK, CUSTOMER_EXCELLENT,
    CREDIT_LIMIT_REACHED, REPEATED_DEFAULT, PAYMENT_OVERDUE) are raised by
    that same database layer. This service only reads them back (via
    IssueRepository) to build the credit-history timeline.

What this service DOES compute, in Python, from already-stored numbers:
  * ``reason`` / ``risk_indicators`` -- a human-readable explanation of the
    *current* stored score, built from the same counters the DB function
    used. This is descriptive, not predictive: it never re-derives the score.
  * ``recommendation`` -- a short suggestion string keyed off credit_level.
  * The credit simulation (``simulate_credit``) -- a pure what-if projection
    of a requested debt amount against the customer's current standing. It
    answers "would this be approved right now", it does not change anything.

Controllers that call this service should do nothing but validate the path/
query parameters and delegate -- no credibility math belongs in a router.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.core.business_context import BusinessContext
from app.core.exceptions import NotFoundError
from app.core.phone import resolve_phone_number
from app.models.customer import Customer
from app.repositories.customer_repository import CustomerRepository
from app.repositories.issue_repository import IssueRepository
from app.repositories.payment_repository import PaymentRepository
from app.schemas.customer import (
    CreditEventListOut,
    CreditEventOut,
    CreditSimulationOut,
    CustomerCreateRequest,
    CustomerCreditListOut,
    CustomerCreditOut,
    CustomerLedgerEntryOut,
    CustomerLedgerListOut,
    CustomerListOut,
    CustomerOut,
    CustomerPaymentAllocationOut,
    CustomerPaymentHistoryEntryOut,
    CustomerPaymentHistoryListOut,
    CustomerUpdateRequest,
    LedgerPaymentOut,
)

_ZERO = Decimal("0")

# Recommendation copy per credit_level -- single source of truth so
# CustomerCreditOut and CreditSimulationOut never disagree with each other.
_RECOMMENDATIONS: dict[str, str] = {
    "UNSCORED": "Not enough history yet — treat as a new customer.",
    "BLOCKED": "Avoid giving new debt.",
    "HIGH_RISK": "Give debt with caution; consider part-payment upfront.",
    "AVERAGE": "Safe for small debt amounts, within the recommended limit.",
    "GOOD": "Safe to extend debt within the recommended limit.",
    "EXCELLENT": "Trusted customer; safe for a higher debt amount.",
}


class CustomerService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = CustomerRepository(ctx.session)

    # ----- CRUD --------------------------------------------------------------- #
    async def create(self, data: CustomerCreateRequest) -> CustomerOut:
        phone_number = await resolve_phone_number(
            self._ctx.session,
            phone_country_id=data.phone_country_id,
            phone_local_number=data.phone_local_number,
            legacy_phone_number=data.phone_number,
        )
        customer = await self._repo.create(
            business_id=self._ctx.business_id,
            full_name=data.full_name,
            phone_number=phone_number,
            notes=data.notes,
        )
        return CustomerOut.model_validate(customer)

    async def update(
        self, customer_id: uuid.UUID, data: CustomerUpdateRequest
    ) -> CustomerOut:
        customer = await self._get_or_404(customer_id)
        if data.full_name is not None:
            customer.full_name = data.full_name
        if data.phone_country_id is not None:
            customer.phone_number = await resolve_phone_number(
                self._ctx.session,
                phone_country_id=data.phone_country_id,
                phone_local_number=data.phone_local_number,
                legacy_phone_number=data.phone_number,
            )
        elif data.phone_number is not None:
            customer.phone_number = data.phone_number
        if data.notes is not None:
            customer.notes = data.notes
        if data.status is not None:
            customer.status = data.status
        customer.updated_at = datetime.now(timezone.utc)
        await self._repo.flush()
        await self._ctx.session.refresh(customer)
        return CustomerOut.model_validate(customer)

    async def archive(self, customer_id: uuid.UUID) -> CustomerOut:
        """Soft-delete: set status = ARCHIVED. Financial history is preserved."""
        customer = await self._get_or_404(customer_id)
        customer.status = "ARCHIVED"
        customer.updated_at = datetime.now(timezone.utc)
        await self._repo.flush()
        await self._ctx.session.refresh(customer)
        return CustomerOut.model_validate(customer)

    async def get(self, customer_id: uuid.UUID) -> CustomerOut:
        customer = await self._get_or_404(customer_id)
        return CustomerOut.model_validate(customer)

    async def search(
        self,
        *,
        search: str | None,
        status: str | None,
        order: str,
        limit: int,
        offset: int,
        credit_level: str | None = None,
        is_blocked: bool | None = None,
        has_active_debt: bool | None = None,
        min_debt_balance: Decimal | None = None,
        max_debt_balance: Decimal | None = None,
        overdue: bool | None = None,
        recently_paid_days: int | None = None,
    ) -> CustomerListOut:
        rows, total = await self._repo.search(
            business_id=self._ctx.business_id,
            search=search,
            status=status,
            order=order,
            limit=limit,
            offset=offset,
            credit_level=credit_level,
            is_blocked=is_blocked,
            has_active_debt=has_active_debt,
            min_debt_balance=min_debt_balance,
            max_debt_balance=max_debt_balance,
            overdue=overdue,
            recently_paid_days=recently_paid_days,
        )
        return CustomerListOut(
            items=[CustomerOut.model_validate(c) for c in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    # ----- Customer Credibility Engine: profile ------------------------------- #
    async def get_credit(self, customer_id: uuid.UUID) -> CustomerCreditOut:
        """The full credit profile: score, level, limit, outstanding balance,
        payment behaviour counters, oldest unpaid debt, and a plain-language
        recommendation + reason + risk indicators.
        """
        customer = await self._get_or_404(customer_id)
        return self._to_credit_out(customer)

    async def list_credit_board(
        self,
        *,
        credit_level: str | None = None,
        is_blocked: bool | None = None,
        has_active_debt: bool | None = None,
        min_debt_balance: Decimal | None = None,
        max_debt_balance: Decimal | None = None,
        overdue: bool | None = None,
        recently_paid_days: int | None = None,
        order: str = "score_asc",
        limit: int = 20,
        offset: int = 0,
    ) -> CustomerCreditListOut:
        """Business-wide credit board -- backs ``/customers/high-risk``,
        ``/customers/excellent`` and ``/customers/recommendation``.
        ``order="score_asc"`` (default) surfaces the riskiest customers first,
        which is what an owner deciding where to focus collections wants.
        """
        rows, total = await self._repo.search(
            business_id=self._ctx.business_id,
            search=None,
            status="ACTIVE",
            order=order,
            limit=limit,
            offset=offset,
            credit_level=credit_level,
            is_blocked=is_blocked,
            has_active_debt=has_active_debt,
            min_debt_balance=min_debt_balance,
            max_debt_balance=max_debt_balance,
            overdue=overdue,
            recently_paid_days=recently_paid_days,
        )
        return CustomerCreditListOut(
            items=[self._to_credit_out(c) for c in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def list_high_risk(
        self, *, limit: int, offset: int
    ) -> CustomerCreditListOut:
        return await self.list_credit_board(
            credit_level="HIGH_RISK", order="score_asc", limit=limit, offset=offset
        )

    async def list_excellent(
        self, *, limit: int, offset: int
    ) -> CustomerCreditListOut:
        return await self.list_credit_board(
            credit_level="EXCELLENT", order="score_desc", limit=limit, offset=offset
        )

    # ----- Debt ledger (GET /customers/{id}/ledger) --------------------------- #
    async def get_ledger(
        self,
        customer_id: uuid.UUID,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> CustomerLedgerListOut:
        await self._get_or_404(customer_id)  # 404 before leaking an empty list
        rows, total = await self._repo.get_ledger(
            business_id=self._ctx.business_id,
            customer_id=customer_id,
            status=status,
            limit=limit,
            offset=offset,
        )

        # Bulk-fetch "which payment(s) closed this debt" for every row on
        # this page in one query (no N+1) -- empty for still-open rows.
        payment_repo = PaymentRepository(self._ctx.session)
        allocations_by_ledger = await payment_repo.get_allocations_for_ledger_ids(
            self._ctx.business_id, [r.id for r, _receipt_number in rows]
        )

        return CustomerLedgerListOut(
            items=[
                CustomerLedgerEntryOut(
                    id=r.id,
                    sale_id=r.sale_id,
                    receipt_number=receipt_number,
                    original_amount=r.original_amount,
                    paid_amount=r.paid_amount,
                    remaining_amount=(
                        r.remaining_amount
                        if r.remaining_amount is not None
                        else (r.original_amount - r.paid_amount)
                    ),
                    incurred_at=r.incurred_at,
                    due_date=r.due_date,
                    fully_paid_at=r.fully_paid_at,
                    status=r.status,
                    risk_flag=r.risk_flag,
                    payments=[
                        LedgerPaymentOut(
                            payment_id=payment.id,
                            amount=alloc.allocated_amount,
                            occurred_at=payment.occurred_at,
                            note=payment.note,
                        )
                        for alloc, payment in allocations_by_ledger.get(r.id, [])
                    ],
                )
                for r, receipt_number in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    # ----- Payment + allocation history (GET /customers/{id}/payments) -------- #
    async def get_payment_history(
        self, customer_id: uuid.UUID, *, limit: int, offset: int
    ) -> CustomerPaymentHistoryListOut:
        await self._get_or_404(customer_id)
        payment_repo = PaymentRepository(self._ctx.session)
        rows, total = await payment_repo.list_payments_with_allocations(
            business_id=self._ctx.business_id,
            customer_id=customer_id,
            limit=limit,
            offset=offset,
        )
        items = []
        for payment, allocations in rows:
            items.append(
                CustomerPaymentHistoryEntryOut(
                    id=payment.id,
                    amount=payment.amount,
                    occurred_at=payment.occurred_at,
                    note=payment.note,
                    is_correction=payment.is_correction,
                    allocations=[
                        CustomerPaymentAllocationOut(
                            debt_ledger_id=alloc.debt_ledger_id,
                            sale_id=ledger.sale_id,
                            allocated_amount=alloc.allocated_amount,
                            ledger_status=ledger.status,
                            ledger_remaining=(
                                ledger.remaining_amount
                                if ledger.remaining_amount is not None
                                else (ledger.original_amount - ledger.paid_amount)
                            ),
                        )
                        for alloc, ledger in allocations
                    ],
                )
            )
        return CustomerPaymentHistoryListOut(
            items=items, total=total, limit=limit, offset=offset
        )

    # ----- Credit event timeline (GET /customers/credit-history) ------------- #
    async def get_credit_history(
        self,
        *,
        customer_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> CreditEventListOut:
        if customer_id is not None:
            await self._get_or_404(customer_id)
        issue_repo = IssueRepository(self._ctx.session)
        rows, total = await issue_repo.list_credit_events(
            business_id=self._ctx.business_id,
            customer_id=customer_id,
            limit=limit,
            offset=offset,
        )
        return CreditEventListOut(
            items=[
                CreditEventOut(
                    issue_id=issue.id,
                    customer_id=issue.ref_customer_id,
                    customer_name=name,
                    event_code=itype.code,
                    headline=issue.headline,
                    body_text=issue.body_text,
                    severity=issue.severity,
                    data_snapshot=issue.data_snapshot,
                    status=issue.status,
                    created_at=issue.created_at,
                )
                for issue, itype, name in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    # ----- Credit simulation (POST /customers/{id}/simulate-credit) ---------- #
    async def simulate_credit(
        self, customer_id: uuid.UUID, requested_amount: Decimal
    ) -> CreditSimulationOut:
        """"If I sell this customer {requested_amount} on credit right now,
        what happens?" A pure projection -- never writes anything, never
        recomputes the score. Mirrors exactly the check ``SaleService`` runs
        for a real ON_DEBT sale, so the frontend can show this before the
        cashier commits to the sale.
        """
        customer = await self._get_or_404(customer_id)

        projected_balance = customer.debt_balance + requested_amount
        would_exceed_limit = (
            customer.recommended_credit_limit > 0
            and projected_balance > customer.recommended_credit_limit
        )
        is_active = customer.status == "ACTIVE"
        would_be_approved = (
            is_active
            and not customer.is_credit_blocked
            and not would_exceed_limit
        )

        available = max(
            customer.recommended_credit_limit - customer.debt_balance, _ZERO
        )

        if not is_active:
            recommendation = "This customer's profile is archived — reactivate it first."
        elif customer.is_credit_blocked:
            recommendation = _RECOMMENDATIONS["BLOCKED"]
        elif would_exceed_limit:
            recommendation = (
                f"This would bring their debt to {projected_balance}, above the "
                f"recommended limit of {customer.recommended_credit_limit}. "
                "Consider a smaller amount or partial upfront payment."
            )
        else:
            recommendation = _RECOMMENDATIONS.get(
                customer.credit_level, _RECOMMENDATIONS["UNSCORED"]
            )

        return CreditSimulationOut(
            customer_id=customer.id,
            requested_amount=requested_amount,
            would_be_approved=would_be_approved,
            would_exceed_limit=would_exceed_limit,
            is_credit_blocked=customer.is_credit_blocked,
            current_score=customer.credit_score,
            current_level=customer.credit_level,
            current_debt_balance=customer.debt_balance,
            projected_debt_balance=projected_balance,
            recommended_credit_limit=customer.recommended_credit_limit,
            remaining_available_credit=available,
            recommendation=recommendation,
        )

    # ----- internal helpers ---------------------------------------------------- #
    async def _get_or_404(self, customer_id: uuid.UUID) -> Customer:
        customer = await self._repo.get_by_id(self._ctx.business_id, customer_id)
        if customer is None:
            raise NotFoundError("Customer not found.", code="customer_not_found")
        return customer

    @classmethod
    def _to_credit_out(cls, customer: Customer) -> CustomerCreditOut:
        available = max(
            customer.recommended_credit_limit - customer.debt_balance, _ZERO
        )
        days_overdue = 0
        if customer.oldest_unpaid_at is not None:
            days_overdue = max(
                (datetime.now(timezone.utc) - customer.oldest_unpaid_at).days, 0
            )
        reason, risk_indicators = cls._derive_reason_and_risk(customer, days_overdue)
        return CustomerCreditOut(
            customer_id=customer.id,
            business_id=customer.business_id,
            full_name=customer.full_name,
            phone_number=customer.phone_number,
            credit_score=customer.credit_score,
            credit_level=customer.credit_level,
            is_credit_blocked=customer.is_credit_blocked,
            debt_balance=customer.debt_balance,
            recommended_credit_limit=customer.recommended_credit_limit,
            available_credit=available,
            average_payment_days=customer.average_payment_days,
            on_time_payment_count=customer.on_time_payment_count,
            late_payment_count=customer.late_payment_count,
            fully_paid_debt_count=customer.fully_paid_debt_count,
            days_overdue=days_overdue,
            last_credit_review=customer.last_credit_review,
            recommendation=_RECOMMENDATIONS.get(
                customer.credit_level, _RECOMMENDATIONS["UNSCORED"]
            ),
            reason=reason,
            risk_indicators=risk_indicators,
        )

    @staticmethod
    def _derive_reason_and_risk(
        customer: Customer, days_overdue: int
    ) -> tuple[str, list[str]]:
        """Explain the *current, already-computed* score/level using the same
        counters the database function used to arrive at it. This never
        re-derives the score -- it only narrates it. Zero extra queries: every
        input here is already a loaded column on ``customer``.
        """
        risk_indicators: list[str] = []

        if customer.credit_level == "UNSCORED":
            risk_indicators.append("No repayment history yet")
            return (
                "No debt history yet — treated as a new customer with no "
                "credit score.",
                risk_indicators,
            )

        if customer.is_credit_blocked:
            risk_indicators.append("Credit blocked")
        if customer.credit_level == "HIGH_RISK":
            risk_indicators.append("High risk credit level")
        if customer.late_payment_count > 0:
            risk_indicators.append(
                f"{customer.late_payment_count} late payment(s) on record"
            )
        if customer.debt_balance > 0 and days_overdue > 30:
            risk_indicators.append(
                f"Oldest unpaid debt is {days_overdue} days old"
            )
        if (
            customer.recommended_credit_limit > 0
            and customer.debt_balance >= customer.recommended_credit_limit
        ):
            risk_indicators.append("Debt balance has reached the recommended limit")

        reason = (
            f"Score reflects {customer.fully_paid_debt_count} fully paid "
            f"debt(s), {customer.on_time_payment_count} on-time payment(s), "
            f"and {customer.late_payment_count} late payment(s)."
        )
        if risk_indicators:
            reason += " " + " ".join(f"{r}." for r in risk_indicators)

        return reason, risk_indicators
