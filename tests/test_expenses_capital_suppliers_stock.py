"""CRUD/filter tests for Expenses, Capital, Suppliers, and Stock Purchases —
the remaining modules under the "every module supports CRUD + pagination +
sorting + searching + filtering" general rule.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.capital import CapitalTransactionCreateRequest
from app.schemas.expense import ExpenseCreateRequest
from app.schemas.stock_purchase import StockPurchaseCreateRequest, StockPurchaseItemCreate
from app.schemas.supplier import SupplierCreateRequest, SupplierUpdateRequest
from app.services.capital_service import CapitalService
from app.services.expense_service import ExpenseService
from app.services.stock_purchase_service import StockPurchaseService
from app.services.supplier_service import SupplierService


# --------------------------------------------------------------------------- #
# Expenses
# --------------------------------------------------------------------------- #
async def test_expense_create_and_get(business_ctx):
    service = ExpenseService(business_ctx)
    created = await service.create_expense(
        ExpenseCreateRequest(category_id=1, amount=Decimal("15000"), note="Shop rent")
    )
    fetched = await service.get_expense(created.expense.id)
    assert fetched.amount == Decimal("15000")


async def test_expense_get_not_found(business_ctx):
    service = ExpenseService(business_ctx)
    with pytest.raises(NotFoundError):
        await service.get_expense(uuid.uuid4())


async def test_expense_invalid_category_rejected(business_ctx):
    service = ExpenseService(business_ctx)
    with pytest.raises(ValidationError):
        await service.create_expense(ExpenseCreateRequest(category_id=99999, amount=Decimal("1000")))


async def test_expense_filter_by_group_and_search(business_ctx):
    service = ExpenseService(business_ctx)
    await service.create_expense(ExpenseCreateRequest(category_id=1, amount=Decimal("5000"), note="rent payment"))
    await service.create_expense(ExpenseCreateRequest(category_id=2, amount=Decimal("2000"), note="school fees"))

    business_only = await service.list_expenses(
        category_id=None, group_id=1, date_from=None, date_to=None, limit=20, offset=0
    )
    assert business_only.total == 1
    assert business_only.items[0].note == "rent payment"

    search_result = await service.list_expenses(
        category_id=None, group_id=None, search="school", date_from=None, date_to=None, limit=20, offset=0
    )
    assert search_result.total == 1


async def test_expense_filter_by_date_range(business_ctx):
    service = ExpenseService(business_ctx)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)
    await service.create_expense(ExpenseCreateRequest(category_id=1, amount=Decimal("1000"), occurred_at=old))
    await service.create_expense(ExpenseCreateRequest(category_id=1, amount=Decimal("2000"), occurred_at=now))

    recent = await service.list_expenses(
        category_id=None, group_id=None, date_from=now - timedelta(days=1), date_to=None, limit=20, offset=0
    )
    assert recent.total == 1
    assert recent.items[0].amount == Decimal("2000")


async def test_expense_sort_by_amount(business_ctx):
    service = ExpenseService(business_ctx)
    await service.create_expense(ExpenseCreateRequest(category_id=1, amount=Decimal("500")))
    await service.create_expense(ExpenseCreateRequest(category_id=1, amount=Decimal("9000")))
    result = await service.list_expenses(
        category_id=None, group_id=None, date_from=None, date_to=None, order="amount_desc", limit=20, offset=0
    )
    assert result.items[0].amount == Decimal("9000")


# --------------------------------------------------------------------------- #
# Capital
# --------------------------------------------------------------------------- #
async def test_capital_create_money_in_and_get(business_ctx):
    service = CapitalService(business_ctx)
    resp = await service.create_capital_transaction(
        CapitalTransactionCreateRequest(type_code="MONEY_IN", amount=Decimal("20000"))
    )
    fetched = await service.get_capital_transaction(resp.capital_transaction.id)
    assert fetched.amount == Decimal("20000")
    assert fetched.direction == "I"


async def test_capital_opening_balance_rejected_at_schema_layer(business_ctx):
    """OPENING_BALANCE isn't even a valid ``type_code`` value for this
    request schema (see CapitalTypeCode) — Pydantic rejects it before the
    service's own defense-in-depth check would ever run."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        CapitalTransactionCreateRequest(type_code="OPENING_BALANCE", amount=Decimal("1000"))


async def test_capital_filter_by_type(business_ctx):
    service = CapitalService(business_ctx)
    await service.create_capital_transaction(CapitalTransactionCreateRequest(type_code="MONEY_IN", amount=Decimal("5000")))
    await service.create_capital_transaction(CapitalTransactionCreateRequest(type_code="MONEY_OUT", amount=Decimal("2000")))
    money_in = await service.list_capital_transactions(type_code="MONEY_IN", date_from=None, date_to=None, limit=20, offset=0)
    money_out = await service.list_capital_transactions(type_code="MONEY_OUT", date_from=None, date_to=None, limit=20, offset=0)
    assert money_in.total == 1
    assert money_out.total == 1


async def test_capital_not_found(business_ctx):
    service = CapitalService(business_ctx)
    with pytest.raises(NotFoundError):
        await service.get_capital_transaction(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Suppliers
# --------------------------------------------------------------------------- #
async def test_supplier_crud(business_ctx):
    service = SupplierService(business_ctx)
    created = await service.create(SupplierCreateRequest(name="Acme Distributors"))
    fetched = await service.get(created.id)
    assert fetched.name == "Acme Distributors"

    updated = await service.update(created.id, SupplierUpdateRequest(notes="Reliable"))
    assert updated.notes == "Reliable"

    archived = await service.archive(created.id)
    assert archived.status == "ARCHIVED"


async def test_supplier_search_and_pagination(business_ctx):
    service = SupplierService(business_ctx)
    for name in ["Zeta Supplies", "Alpha Traders", "Beta Wholesalers"]:
        await service.create(SupplierCreateRequest(name=name))
    result = await service.search(search=None, status="ACTIVE", order="name_asc", limit=2, offset=0)
    assert result.total == 3
    assert len(result.items) == 2
    assert result.items[0].name == "Alpha Traders"  # name_asc


async def test_supplier_not_found(business_ctx):
    service = SupplierService(business_ctx)
    with pytest.raises(NotFoundError):
        await service.get(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Stock Purchases
# --------------------------------------------------------------------------- #
async def test_stock_purchase_create_and_get(business_ctx):
    supplier = await SupplierService(business_ctx).create(SupplierCreateRequest(name="Farm Supplies Ltd"))
    service = StockPurchaseService(business_ctx)
    resp = await service.create_purchase(
        StockPurchaseCreateRequest(
            supplier_id=supplier.id,
            items=[StockPurchaseItemCreate(product_name="Maize Flour 50kg", quantity=Decimal("10"), total_item_cost=Decimal("500000"))],
        )
    )
    assert resp.purchase.total_buying_cost == Decimal("500000")
    fetched = await service.get_purchase(resp.purchase.id)
    assert fetched.supplier_id == supplier.id


async def test_stock_purchase_get_not_found(business_ctx):
    service = StockPurchaseService(business_ctx)
    with pytest.raises(NotFoundError):
        await service.get_purchase(uuid.uuid4())


async def test_stock_purchase_filter_by_supplier(business_ctx):
    s1 = await SupplierService(business_ctx).create(SupplierCreateRequest(name="Supplier One"))
    s2 = await SupplierService(business_ctx).create(SupplierCreateRequest(name="Supplier Two"))
    service = StockPurchaseService(business_ctx)
    await service.create_purchase(
        StockPurchaseCreateRequest(
            supplier_id=s1.id,
            items=[StockPurchaseItemCreate(product_name="Item A", quantity=Decimal("1"), total_item_cost=Decimal("1000"))],
        )
    )
    await service.create_purchase(
        StockPurchaseCreateRequest(
            supplier_id=s2.id,
            items=[StockPurchaseItemCreate(product_name="Item B", quantity=Decimal("1"), total_item_cost=Decimal("2000"))],
        )
    )
    result = await service.list_purchases(supplier_id=s1.id, date_from=None, date_to=None, limit=20, offset=0)
    assert result.total == 1
    assert result.items[0].supplier_id == s1.id


async def test_stock_purchase_search_by_note(business_ctx):
    service = StockPurchaseService(business_ctx)
    await service.create_purchase(
        StockPurchaseCreateRequest(
            supplier_name_free="Roadside Vendor",
            note="urgent restock",
            items=[StockPurchaseItemCreate(product_name="Item", quantity=Decimal("1"), total_item_cost=Decimal("1000"))],
        )
    )
    result = await service.list_purchases(supplier_id=None, search="urgent", date_from=None, date_to=None, limit=20, offset=0)
    assert result.total == 1
