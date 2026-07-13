"""Product & Category routers (per blueprint F5). Require X-Business-Id header.

Categories:
    POST  /product-categories          create (custom, per business)
    GET   /product-categories          list (system defaults + own)
    PATCH /product-categories/{id}      update (own only)

Products:
    POST   /products                    create (returns product + soft warnings)
    GET    /products                    search/list
    GET    /products/{id}               detail
    PATCH  /products/{id}               update
    POST   /products/{id}/archive       archive (soft delete)
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.core.master_data_deps import CategoryServiceDep, ProductServiceDep
from app.schemas.product import (
    CategoryCreateRequest,
    CategoryOut,
    CategoryUpdateRequest,
    ProductCreateRequest,
    ProductCreateResponse,
    ProductListOut,
    ProductOut,
    ProductUpdateRequest,
)

# ----- Categories ----------------------------------------------------------- #
category_router = APIRouter(prefix="/product-categories", tags=["Product Categories"])


@category_router.post(
    "", response_model=CategoryOut, status_code=status.HTTP_201_CREATED
)
async def create_category(
    payload: CategoryCreateRequest, service: CategoryServiceDep
) -> CategoryOut:
    return await service.create(payload)


@category_router.get("", response_model=list[CategoryOut])
async def list_categories(
    service: CategoryServiceDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[CategoryOut]:
    return await service.list(include_inactive=include_inactive)


@category_router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: int, payload: CategoryUpdateRequest, service: CategoryServiceDep
) -> CategoryOut:
    return await service.update(category_id, payload)


# ----- Products ------------------------------------------------------------- #
product_router = APIRouter(prefix="/products", tags=["Products"])


@product_router.post(
    "", response_model=ProductCreateResponse, status_code=status.HTTP_201_CREATED
)
async def create_product(
    payload: ProductCreateRequest, service: ProductServiceDep
) -> ProductCreateResponse:
    return await service.create(payload)


@product_router.get("", response_model=ProductListOut)
async def search_products(
    service: ProductServiceDep,
    search: Annotated[str | None, Query(max_length=300)] = None,
    category_id: Annotated[int | None, Query(ge=1)] = None,
    status_filter: Annotated[
        Literal["ACTIVE", "ARCHIVED"] | None, Query(alias="status")
    ] = "ACTIVE",
    stock_status: Annotated[
        Literal["OUT_OF_STOCK", "LOW_STOCK", "OK"] | None,
        Query(description="Filter by stock level bucket"),
    ] = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    supplier_id: Annotated[
        uuid.UUID | None,
        Query(description="Products with at least one stock purchase from this supplier"),
    ] = None,
    order: Annotated[
        Literal["name_asc", "stock_asc", "stock_desc", "price_asc", "price_desc", "recent"],
        Query(),
    ] = "name_asc",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductListOut:
    return await service.search(
        search=search,
        category_id=category_id,
        status=status_filter,
        stock_status=stock_status,
        min_price=min_price,
        max_price=max_price,
        supplier_id=supplier_id,
        order=order,
        limit=limit,
        offset=offset,
    )


@product_router.get("/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: uuid.UUID, service: ProductServiceDep
) -> ProductOut:
    return await service.get(product_id)


@product_router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdateRequest,
    service: ProductServiceDep,
) -> ProductOut:
    return await service.update(product_id, payload)


@product_router.post("/{product_id}/archive", response_model=ProductOut)
async def archive_product(
    product_id: uuid.UUID, service: ProductServiceDep
) -> ProductOut:
    return await service.archive(product_id)
