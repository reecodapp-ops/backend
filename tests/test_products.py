"""Product module tests — CRUD, filtering (category/stock level/price/status),
pagination, and duplicate-name soft warnings.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.product import (
    CategoryCreateRequest,
    ProductCreateRequest,
    ProductUpdateRequest,
)
from app.services.product_service import CategoryService, ProductService


async def test_create_product(business_ctx):
    service = ProductService(business_ctx)
    resp = await service.create(
        ProductCreateRequest(name="Sugar 1kg", selling_price=Decimal("3000"), stock_qty=Decimal("10"))
    )
    assert resp.product.name == "Sugar 1kg"
    assert resp.product.stock_on_hand == Decimal("10")
    assert resp.warnings == []


async def test_create_product_duplicate_name_warns_but_does_not_block(business_ctx):
    service = ProductService(business_ctx)
    await service.create(ProductCreateRequest(name="Soap Bar"))
    resp = await service.create(ProductCreateRequest(name="Soap Bar Large"))
    assert resp.product.id is not None
    assert any(w.code == "DUPLICATE_ITEM" for w in resp.warnings)


async def test_update_product_price_updates_avg_cost_baseline(business_ctx):
    service = ProductService(business_ctx)
    created = (await service.create(ProductCreateRequest(name="Rice", buying_price=Decimal("5000")))).product
    updated = await service.update(
        created.id, ProductUpdateRequest(buying_price=Decimal("5500"))
    )
    assert updated.buying_price == Decimal("5500")
    assert updated.avg_buying_cost == Decimal("5500")


async def test_archive_product_soft_delete(business_ctx):
    service = ProductService(business_ctx)
    created = (await service.create(ProductCreateRequest(name="Candle"))).product
    archived = await service.archive(created.id)
    assert archived.status == "ARCHIVED"


async def test_get_product_not_found(business_ctx):
    service = ProductService(business_ctx)
    with pytest.raises(NotFoundError):
        await service.get(uuid.uuid4())


async def test_invalid_category_rejected(business_ctx):
    service = ProductService(business_ctx)
    with pytest.raises(ValidationError):
        await service.create(ProductCreateRequest(name="Bad Category Item", category_id=99999))


async def test_category_create_and_use(business_ctx):
    cat_service = CategoryService(business_ctx)
    category = await cat_service.create(CategoryCreateRequest(name="Beverages"))
    prod_service = ProductService(business_ctx)
    resp = await prod_service.create(
        ProductCreateRequest(name="Soda", category_id=category.id)
    )
    assert resp.product.category_id == category.id


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #
async def test_filter_out_of_stock_and_low_stock(business_ctx):
    service = ProductService(business_ctx)
    await service.create(ProductCreateRequest(name="Out", stock_qty=Decimal("0"), low_stock_level=Decimal("5")))
    await service.create(ProductCreateRequest(name="Low", stock_qty=Decimal("2"), low_stock_level=Decimal("5")))
    await service.create(ProductCreateRequest(name="Ok", stock_qty=Decimal("50"), low_stock_level=Decimal("5")))

    out_of_stock = await service.search(
        search=None, category_id=None, status="ACTIVE", stock_status="OUT_OF_STOCK", limit=20, offset=0
    )
    assert [p.name for p in out_of_stock.items] == ["Out"]

    low_stock = await service.search(
        search=None, category_id=None, status="ACTIVE", stock_status="LOW_STOCK", limit=20, offset=0
    )
    assert [p.name for p in low_stock.items] == ["Low"]

    ok_stock = await service.search(
        search=None, category_id=None, status="ACTIVE", stock_status="OK", limit=20, offset=0
    )
    assert [p.name for p in ok_stock.items] == ["Ok"]


async def test_filter_price_range(business_ctx):
    service = ProductService(business_ctx)
    await service.create(ProductCreateRequest(name="Cheap", selling_price=Decimal("500")))
    await service.create(ProductCreateRequest(name="Mid", selling_price=Decimal("5000")))
    await service.create(ProductCreateRequest(name="Expensive", selling_price=Decimal("50000")))

    result = await service.search(
        search=None,
        category_id=None,
        status="ACTIVE",
        min_price=Decimal("1000"),
        max_price=Decimal("10000"),
        limit=20,
        offset=0,
    )
    assert [p.name for p in result.items] == ["Mid"]


async def test_filter_by_category(business_ctx):
    cat_service = CategoryService(business_ctx)
    drinks = await cat_service.create(CategoryCreateRequest(name="Drinks"))
    food = await cat_service.create(CategoryCreateRequest(name="Food"))
    prod_service = ProductService(business_ctx)
    await prod_service.create(ProductCreateRequest(name="Cola", category_id=drinks.id))
    await prod_service.create(ProductCreateRequest(name="Bread", category_id=food.id))

    result = await prod_service.search(
        search=None, category_id=drinks.id, status="ACTIVE", limit=20, offset=0
    )
    assert [p.name for p in result.items] == ["Cola"]


async def test_search_text(business_ctx):
    service = ProductService(business_ctx)
    await service.create(ProductCreateRequest(name="Blue Band Margarine"))
    await service.create(ProductCreateRequest(name="Peanut Butter"))
    result = await service.search(
        search="Band", category_id=None, status="ACTIVE", limit=20, offset=0
    )
    assert result.total == 1
    assert result.items[0].name == "Blue Band Margarine"


async def test_sorting_by_price(business_ctx):
    service = ProductService(business_ctx)
    await service.create(ProductCreateRequest(name="A", selling_price=Decimal("300")))
    await service.create(ProductCreateRequest(name="B", selling_price=Decimal("100")))
    await service.create(ProductCreateRequest(name="C", selling_price=Decimal("200")))
    result = await service.search(
        search=None, category_id=None, status="ACTIVE", order="price_asc", limit=20, offset=0
    )
    assert [p.name for p in result.items] == ["B", "C", "A"]


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #
async def test_product_pagination(business_ctx):
    service = ProductService(business_ctx)
    for i in range(7):
        await service.create(ProductCreateRequest(name=f"Item {i}"))
    page1 = await service.search(
        search=None, category_id=None, status="ACTIVE", order="name_asc", limit=3, offset=0
    )
    page2 = await service.search(
        search=None, category_id=None, status="ACTIVE", order="name_asc", limit=3, offset=3
    )
    assert page1.total == 7
    assert len(page1.items) == 3
    assert len(page2.items) == 3
    assert {p.id for p in page1.items}.isdisjoint({p.id for p in page2.items})
