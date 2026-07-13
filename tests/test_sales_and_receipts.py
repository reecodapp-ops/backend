"""Sales module tests — creation (cash + debt), credit-sale validation/warnings,
filtering, and the full Receipts feature set (preview, share + WhatsApp link,
regenerate).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.models.customer import Customer
from app.schemas.customer import CustomerCreateRequest
from app.schemas.sale import SaleCreateRequest, SaleItemCreate
from app.services.customer_service import CustomerService
from app.services.sale_service import SaleService


async def _make_customer(business_ctx, **overrides):
    service = CustomerService(business_ctx)
    return await service.create(CustomerCreateRequest(full_name=overrides.pop("full_name", "Test Customer")))


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #
async def test_create_cash_sale(business_ctx):
    service = SaleService(business_ctx)
    resp = await service.create_sale(
        SaleCreateRequest(
            payment_type="CASH",
            items=[SaleItemCreate(product_name="Bread", quantity=Decimal("2"), unit_price=Decimal("2500"))],
        )
    )
    assert resp.sale.total_amount == Decimal("5000")
    assert resp.sale.outstanding_amount == Decimal("0")
    assert resp.sale.receipt_number.startswith("RCP-")
    assert resp.warnings == []


async def test_create_debt_sale_requires_customer(business_ctx):
    service = SaleService(business_ctx)
    with pytest.raises(ValidationError) as exc:
        await service.create_sale(
            SaleCreateRequest(
                payment_type="ON_DEBT",
                items=[SaleItemCreate(product_name="Milk", quantity=Decimal("1"), unit_price=Decimal("2000"))],
            )
        )
    assert exc.value.code == "customer_required_for_debt"


async def test_create_debt_sale_unknown_customer_rejected(business_ctx):
    import uuid

    service = SaleService(business_ctx)
    with pytest.raises(ValidationError) as exc:
        await service.create_sale(
            SaleCreateRequest(
                payment_type="ON_DEBT",
                customer_id=uuid.uuid4(),
                items=[SaleItemCreate(product_name="Milk", quantity=Decimal("1"), unit_price=Decimal("2000"))],
            )
        )
    assert exc.value.code == "invalid_customer"


async def test_create_debt_sale_blocked_customer_rejected(business_ctx, session):
    customer = await _make_customer(business_ctx)
    row = await session.get(Customer, customer.id)
    row.is_credit_blocked = True
    row.credit_level = "BLOCKED"
    await session.flush()
    await session.commit()

    service = SaleService(business_ctx)
    with pytest.raises(ValidationError) as exc:
        await service.create_sale(
            SaleCreateRequest(
                payment_type="ON_DEBT",
                customer_id=customer.id,
                items=[SaleItemCreate(product_name="Milk", quantity=Decimal("1"), unit_price=Decimal("2000"))],
            )
        )
    assert exc.value.code == "customer_credit_blocked"


async def test_create_debt_sale_archived_customer_rejected(business_ctx, session):
    customer = await _make_customer(business_ctx)
    row = await session.get(Customer, customer.id)
    row.status = "ARCHIVED"
    await session.flush()
    await session.commit()

    service = SaleService(business_ctx)
    with pytest.raises(ValidationError) as exc:
        await service.create_sale(
            SaleCreateRequest(
                payment_type="ON_DEBT",
                customer_id=customer.id,
                items=[SaleItemCreate(product_name="Milk", quantity=Decimal("1"), unit_price=Decimal("2000"))],
            )
        )
    assert exc.value.code == "customer_not_active"


async def test_debt_sale_new_customer_gets_informational_warning(business_ctx):
    customer = await _make_customer(business_ctx)
    service = SaleService(business_ctx)
    resp = await service.create_sale(
        SaleCreateRequest(
            payment_type="ON_DEBT",
            customer_id=customer.id,
            items=[SaleItemCreate(product_name="Sugar", quantity=Decimal("1"), unit_price=Decimal("3000"))],
        )
    )
    assert any(w.code == "NEW_CUSTOMER_NO_HISTORY" for w in resp.warnings)


async def test_debt_sale_exceeding_limit_warns_but_does_not_block(business_ctx, session):
    customer = await _make_customer(business_ctx)
    row = await session.get(Customer, customer.id)
    row.credit_level = "AVERAGE"
    row.debt_balance = Decimal("18000")
    row.recommended_credit_limit = Decimal("20000")
    await session.flush()
    await session.commit()

    service = SaleService(business_ctx)
    resp = await service.create_sale(
        SaleCreateRequest(
            payment_type="ON_DEBT",
            customer_id=customer.id,
            items=[SaleItemCreate(product_name="Flour", quantity=Decimal("1"), unit_price=Decimal("5000"))],
        )
    )
    # sale still succeeds
    assert resp.sale.total_amount == Decimal("5000")
    assert any(w.code == "CREDIT_LIMIT_EXCEEDED" for w in resp.warnings)


async def test_oversell_warning(business_ctx):
    from app.schemas.product import ProductCreateRequest
    from app.services.product_service import ProductService

    product = (
        await ProductService(business_ctx).create(
            ProductCreateRequest(name="Eggs", stock_qty=Decimal("2"), selling_price=Decimal("500"))
        )
    ).product
    service = SaleService(business_ctx)
    resp = await service.create_sale(
        SaleCreateRequest(
            payment_type="CASH",
            items=[
                SaleItemCreate(product_id=product.id, product_name="Eggs", quantity=Decimal("5"), unit_price=Decimal("500"))
            ],
        )
    )
    assert any(w.code == "OVERSELL" for w in resp.warnings)


# --------------------------------------------------------------------------- #
# Filtering / listing
# --------------------------------------------------------------------------- #
async def test_list_sales_filter_by_payment_type(business_ctx):
    customer = await _make_customer(business_ctx)
    service = SaleService(business_ctx)
    await service.create_sale(
        SaleCreateRequest(payment_type="CASH", items=[SaleItemCreate(product_name="A", quantity=Decimal("1"), unit_price=Decimal("1000"))])
    )
    await service.create_sale(
        SaleCreateRequest(
            payment_type="ON_DEBT",
            customer_id=customer.id,
            items=[SaleItemCreate(product_name="B", quantity=Decimal("1"), unit_price=Decimal("2000"))],
        )
    )
    cash_only = await service.list_sales(payment_type="CASH")
    debt_only = await service.list_sales(payment_type="ON_DEBT")
    assert cash_only.total == 1
    assert debt_only.total == 1
    assert cash_only.sales[0].total_amount == Decimal("1000")
    assert debt_only.sales[0].total_amount == Decimal("2000")


async def test_list_sales_filter_by_customer(business_ctx):
    c1 = await _make_customer(business_ctx, full_name="Customer One")
    c2 = await _make_customer(business_ctx, full_name="Customer Two")
    service = SaleService(business_ctx)
    for c in (c1, c1, c2):
        await service.create_sale(
            SaleCreateRequest(
                payment_type="ON_DEBT",
                customer_id=c.id,
                items=[SaleItemCreate(product_name="Item", quantity=Decimal("1"), unit_price=Decimal("1000"))],
            )
        )
    result = await service.list_sales(customer_id=c1.id)
    assert result.total == 2


async def test_list_sales_receipt_number_search(business_ctx):
    service = SaleService(business_ctx)
    resp = await service.create_sale(
        SaleCreateRequest(payment_type="CASH", items=[SaleItemCreate(product_name="X", quantity=Decimal("1"), unit_price=Decimal("500"))])
    )
    receipt_suffix = resp.sale.receipt_number[-4:]
    found = await service.list_sales(receipt_number=receipt_suffix)
    assert found.total == 1


async def test_list_sales_sort_by_amount(business_ctx):
    service = SaleService(business_ctx)
    await service.create_sale(SaleCreateRequest(payment_type="CASH", items=[SaleItemCreate(product_name="Small", quantity=Decimal("1"), unit_price=Decimal("100"))]))
    await service.create_sale(SaleCreateRequest(payment_type="CASH", items=[SaleItemCreate(product_name="Big", quantity=Decimal("1"), unit_price=Decimal("9000"))]))
    result = await service.list_sales(order="amount_desc")
    assert result.sales[0].total_amount == Decimal("9000")


# --------------------------------------------------------------------------- #
# Receipts
# --------------------------------------------------------------------------- #
async def test_receipt_preview(business_ctx):
    service = SaleService(business_ctx)
    resp = await service.create_sale(
        SaleCreateRequest(payment_type="CASH", items=[SaleItemCreate(product_name="Soap", quantity=Decimal("3"), unit_price=Decimal("1500"))])
    )
    receipt = await service.preview_receipt(resp.sale.id)
    assert receipt.business_name == "Test Shop"
    assert receipt.currency_symbol == "USh"
    assert receipt.total_amount == Decimal("4500")
    assert len(receipt.items) == 1
    assert "Thank you" in receipt.footer
    assert receipt.whatsapp_share_url is None


async def test_receipt_share_builds_whatsapp_link(business_ctx):
    service = SaleService(business_ctx)
    resp = await service.create_sale(
        SaleCreateRequest(payment_type="CASH", items=[SaleItemCreate(product_name="Soap", quantity=Decimal("1"), unit_price=Decimal("1500"))])
    )
    receipt = await service.share_receipt(
        resp.sale.id, "CUSTOMER", destination_number="+256 700 111 222"
    )
    assert receipt.receipt_shared_to == "CUSTOMER"
    assert receipt.receipt_shared_at is not None
    assert receipt.whatsapp_share_url is not None
    assert receipt.whatsapp_share_url.startswith("https://wa.me/256700111222")


async def test_receipt_share_without_destination_number_has_no_link(business_ctx):
    service = SaleService(business_ctx)
    resp = await service.create_sale(
        SaleCreateRequest(payment_type="CASH", items=[SaleItemCreate(product_name="X", quantity=Decimal("1"), unit_price=Decimal("100"))])
    )
    receipt = await service.share_receipt(resp.sale.id, "OTHER")
    assert receipt.whatsapp_share_url is None


async def test_receipt_regenerate_issues_new_number(business_ctx):
    service = SaleService(business_ctx)
    resp = await service.create_sale(
        SaleCreateRequest(payment_type="CASH", items=[SaleItemCreate(product_name="X", quantity=Decimal("1"), unit_price=Decimal("100"))])
    )
    original_number = resp.sale.receipt_number
    regenerated = await service.regenerate_receipt(resp.sale.id)
    assert regenerated.receipt_number != original_number

    # the sale itself reflects the new number afterwards
    refreshed = await service.get_sale(resp.sale.id)
    assert refreshed.receipt_number == regenerated.receipt_number
