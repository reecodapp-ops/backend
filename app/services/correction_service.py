"""Correction service.

Per the schema's comment and blueprint F12, the correction workflow is:

  1. Flip the original record's ``is_correction`` flag to TRUE so
     ``reconcile_business_cash`` excludes it from the from-scratch totals.
  2. Insert a new replacement row with the corrected values, ``is_correction =
     FALSE`` (so its aggregate trigger fires and reconcile picks it up), and the
     model-specific ``original_*_id`` pointing back to the original row.
  3. Write the audit row in the ``corrections`` table.
  4. Run ``reconcile_business_cash`` to repair the cached cash_on_hand /
     total_debt_out columns from the canonical (non-correction) set.

Step 4 only runs on PostgreSQL (the function lives in the schema). On SQLite the
correction is still recorded and the original is flagged; cash is unaffected on
SQLite anyway because no aggregate triggers exist.

This service implements the monetary corrections that ``reconcile_business_cash``
already supports: SALE_EDIT (header total), EXPENSE_EDIT, PAYMENT_EDIT,
CAPITAL_EDIT. The other seeded correction types (SALE_ITEM_EDIT, STOCK_EDIT,
PRODUCT_COST_EDIT, DEBT_WRITE_OFF) require entity-specific aggregate repair
beyond cash reconcile and are out of scope here — PRODUCT_COST_EDIT is already
handled in-place by the product service per the blueprint's price-edit rule.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.core.business_context import BusinessContext
from app.core.exceptions import NotFoundError, ValidationError
from app.repositories.correction_repository import CorrectionRepository
from app.schemas.correction import (
    CorrectionCreateRequest,
    CorrectionCreateResponse,
    CorrectionListOut,
    CorrectionNewValues,
    CorrectionOut,
)

_SUPPORTED_CODES = {"SALE_EDIT", "EXPENSE_EDIT", "PAYMENT_EDIT", "CAPITAL_EDIT"}


class CorrectionService:
    def __init__(self, ctx: BusinessContext) -> None:
        self._ctx = ctx
        self._repo = CorrectionRepository(ctx.session)

    async def create_correction(
        self, data: CorrectionCreateRequest
    ) -> CorrectionCreateResponse:
        biz_id = self._ctx.business_id

        if data.correction_type_code not in _SUPPORTED_CODES:
            raise ValidationError(
                "This correction type is not yet supported by the backend.",
                code="unsupported_correction_type",
            )

        ctype = await self._repo.get_correction_type_by_code(data.correction_type_code)
        if ctype is None:
            raise ValidationError(
                "Unknown correction type.", code="invalid_correction_type"
            )

        occurred_at = datetime.now(timezone.utc)

        # Dispatch per type. Each branch:
        #   - fetches the original (business-scoped)
        #   - validates the new_values for this type
        #   - flips original.is_correction = TRUE
        #   - inserts the replacement
        #   - returns (corrected_record_id, field_changed, old_value, new_value)
        if data.correction_type_code == "EXPENSE_EDIT":
            outcome = await self._apply_expense_edit(biz_id, data, occurred_at)
        elif data.correction_type_code == "PAYMENT_EDIT":
            outcome = await self._apply_payment_edit(biz_id, data, occurred_at)
        elif data.correction_type_code == "CAPITAL_EDIT":
            outcome = await self._apply_capital_edit(biz_id, data, occurred_at)
        elif data.correction_type_code == "SALE_EDIT":
            outcome = await self._apply_sale_edit(biz_id, data, occurred_at)
        else:  # pragma: no cover — guarded above
            raise ValidationError(
                "Unsupported correction type.", code="unsupported_correction_type"
            )

        corrected_record_id, field_changed, old_value, new_value = outcome

        audit = await self._repo.insert_correction_audit(
            business_id=biz_id,
            correction_type_id=ctype.id,
            original_record_id=data.original_record_id,
            corrected_record_id=corrected_record_id,
            field_changed=field_changed,
            old_value_text=old_value,
            new_value_text=new_value,
            reason=data.reason,
        )

        # Commit the flag flip + replacement + audit so the trigger and reconcile
        # see a consistent state, then reconcile cash/debt from scratch.
        await self._ctx.session.flush()
        await self._repo.reconcile_business_cash(biz_id)
        await self._ctx.session.flush()

        cash_debt = await self._repo.get_business_cash_and_debt(biz_id)
        cash_after = cash_debt[0] if cash_debt else None
        debt_after = cash_debt[1] if cash_debt else None

        return CorrectionCreateResponse(
            correction=CorrectionOut(
                id=audit.id,
                business_id=audit.business_id,
                correction_type_id=audit.correction_type_id,
                correction_type_code=ctype.code,
                original_record_id=audit.original_record_id,
                corrected_record_id=audit.corrected_record_id,
                field_changed=audit.field_changed,
                old_value_text=audit.old_value_text,
                new_value_text=audit.new_value_text,
                reason=audit.reason,
                created_at=audit.created_at,
            ),
            corrected_record_id=corrected_record_id,
            cash_on_hand_after=cash_after,
            total_debt_out_after=debt_after,
        )

    async def list_corrections(
        self, limit: int, offset: int
    ) -> CorrectionListOut:
        rows, total = await self._repo.list_corrections(
            business_id=self._ctx.business_id, limit=limit, offset=offset
        )
        return CorrectionListOut(
            items=[
                CorrectionOut(
                    id=c.id,
                    business_id=c.business_id,
                    correction_type_id=c.correction_type_id,
                    correction_type_code=ct.code,
                    original_record_id=c.original_record_id,
                    corrected_record_id=c.corrected_record_id,
                    field_changed=c.field_changed,
                    old_value_text=c.old_value_text,
                    new_value_text=c.new_value_text,
                    reason=c.reason,
                    created_at=c.created_at,
                )
                for c, ct in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    # ----- per-type handlers ----------------------------------------------- #
    async def _apply_expense_edit(
        self,
        biz_id: uuid.UUID,
        data: CorrectionCreateRequest,
        occurred_at: datetime,
    ) -> tuple[uuid.UUID, str, str, str]:
        nv = data.new_values
        if nv.amount is None:
            raise ValidationError(
                "EXPENSE_EDIT requires new_values.amount.",
                code="missing_new_amount",
            )
        original = await self._repo.get_expense(biz_id, data.original_record_id)
        if original is None:
            raise NotFoundError(
                "Expense not found.", code="original_record_not_found"
            )
        if original.is_correction:
            raise ValidationError(
                "Original expense has already been corrected.",
                code="already_corrected",
            )
        old_amount = original.amount
        await self._repo.flag_expense_corrected(original.id)
        replacement = await self._repo.insert_expense_replacement(
            business_id=biz_id,
            original=original,
            new_amount=nv.amount,
            new_category_id=nv.category_id,
            new_note=nv.note,
            new_supplier_name=nv.supplier_name,
            occurred_at=occurred_at,
        )
        return replacement.id, "amount", str(old_amount), str(nv.amount)

    async def _apply_payment_edit(
        self,
        biz_id: uuid.UUID,
        data: CorrectionCreateRequest,
        occurred_at: datetime,
    ) -> tuple[uuid.UUID, str, str, str]:
        nv = data.new_values
        if nv.amount is None:
            raise ValidationError(
                "PAYMENT_EDIT requires new_values.amount.",
                code="missing_new_amount",
            )
        original = await self._repo.get_customer_payment(
            biz_id, data.original_record_id
        )
        if original is None:
            raise NotFoundError(
                "Customer payment not found.", code="original_record_not_found"
            )
        if original.is_correction:
            raise ValidationError(
                "Original payment has already been corrected.",
                code="already_corrected",
            )
        old_amount = original.amount
        await self._repo.flag_payment_corrected(original.id)
        replacement = await self._repo.insert_payment_replacement(
            business_id=biz_id,
            original=original,
            new_amount=nv.amount,
            new_note=nv.note,
            occurred_at=occurred_at,
        )
        return replacement.id, "amount", str(old_amount), str(nv.amount)

    async def _apply_capital_edit(
        self,
        biz_id: uuid.UUID,
        data: CorrectionCreateRequest,
        occurred_at: datetime,
    ) -> tuple[uuid.UUID, str, str, str]:
        nv = data.new_values
        if nv.amount is None:
            raise ValidationError(
                "CAPITAL_EDIT requires new_values.amount.",
                code="missing_new_amount",
            )
        original = await self._repo.get_capital_transaction(
            biz_id, data.original_record_id
        )
        if original is None:
            raise NotFoundError(
                "Capital transaction not found.",
                code="original_record_not_found",
            )
        if original.is_correction:
            raise ValidationError(
                "Original capital transaction has already been corrected.",
                code="already_corrected",
            )
        old_amount = original.amount
        await self._repo.flag_capital_corrected(original.id)
        replacement = await self._repo.insert_capital_replacement(
            business_id=biz_id,
            original=original,
            new_amount=nv.amount,
            new_source_type_id=nv.source_type_id,
            new_note=nv.note,
            occurred_at=occurred_at,
        )
        return replacement.id, "amount", str(old_amount), str(nv.amount)

    async def _apply_sale_edit(
        self,
        biz_id: uuid.UUID,
        data: CorrectionCreateRequest,
        occurred_at: datetime,
    ) -> tuple[uuid.UUID, str, str, str]:
        """Header-total adjustment only. Line-item or stock changes are out of
        scope and would need separate aggregate repair for the debt ledger and
        product stock_on_hand."""
        nv = data.new_values
        if nv.total_amount is None:
            raise ValidationError(
                "SALE_EDIT requires new_values.total_amount.",
                code="missing_new_total",
            )
        original = await self._repo.get_sale(biz_id, data.original_record_id)
        if original is None:
            raise NotFoundError(
                "Sale not found.", code="original_record_not_found"
            )
        if original.is_correction:
            raise ValidationError(
                "Original sale has already been corrected.",
                code="already_corrected",
            )
        # Debt sales create ledger rows; correcting a debt sale's total would
        # require updating that ledger row too. Limit to cash sales here for safety.
        if original.payment_type_id == 2:  # ON_DEBT
            raise ValidationError(
                "Debt-sale corrections require ledger repair and are not yet "
                "supported. Reverse via a customer payment instead.",
                code="debt_sale_correction_unsupported",
            )
        old_total = original.total_amount
        await self._repo.flag_sale_corrected(original.id)
        replacement = await self._repo.insert_sale_total_replacement(
            business_id=biz_id,
            original=original,
            new_total_amount=nv.total_amount,
            occurred_at=occurred_at,
        )
        return (
            replacement.id,
            "total_amount",
            str(old_total),
            str(nv.total_amount),
        )
