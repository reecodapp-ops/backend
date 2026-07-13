"""Expense router (per blueprint F9). Requires X-Business-Id header.

    POST  /expenses                 record an expense
    GET   /expenses                 history (filterable, sortable, paginated)
    GET   /expenses/{id}            detail
    GET   /expense-categories       list expense categories visible to this business
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.core.master_data_deps import ExpenseServiceDep
from app.schemas.expense import (
    ExpenseCategoryOut,
    ExpenseCreateRequest,
    ExpenseCreateResponse,
    ExpenseListOut,
    ExpenseOut,
)

router = APIRouter(prefix="/expenses", tags=["Expenses"])
category_router = APIRouter(prefix="/expense-categories", tags=["Expenses"])


@router.post("", response_model=ExpenseCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_expense(
    payload: ExpenseCreateRequest, service: ExpenseServiceDep
) -> ExpenseCreateResponse:
    return await service.create_expense(payload)


@router.get("", response_model=ExpenseListOut)
async def list_expenses(
    service: ExpenseServiceDep,
    category_id: Annotated[int | None, Query(ge=1)] = None,
    group_id: Annotated[
        int | None, Query(ge=1, description="1=Business expense, 2=Personal use")
    ] = None,
    search: Annotated[
        str | None, Query(max_length=200, description="Search note / supplier_name")
    ] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    min_amount: Annotated[Decimal | None, Query(ge=0)] = None,
    max_amount: Annotated[Decimal | None, Query(ge=0)] = None,
    order: Annotated[
        Literal["recent", "oldest", "amount_desc", "amount_asc"], Query()
    ] = "recent",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExpenseListOut:
    return await service.list_expenses(
        category_id=category_id,
        group_id=group_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        min_amount=min_amount,
        max_amount=max_amount,
        order=order,
        limit=limit,
        offset=offset,
    )


@router.get("/{expense_id}", response_model=ExpenseOut)
async def get_expense(expense_id: uuid.UUID, service: ExpenseServiceDep) -> ExpenseOut:
    return await service.get_expense(expense_id)


@category_router.get("", response_model=list[ExpenseCategoryOut])
async def list_expense_categories(
    service: ExpenseServiceDep,
) -> list[ExpenseCategoryOut]:
    return await service.list_categories()
