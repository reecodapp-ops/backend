"""Dependency providers for business-scoped master-data services.

Each provider builds its service from the resolved ``BusinessContext`` (which
has already validated ownership and set the RLS session variable).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.business_context import BusinessCtx
from app.services.capital_service import CapitalService
from app.services.correction_service import CorrectionService
from app.services.customer_service import CustomerService
from app.services.dashboard_service import DashboardService
from app.services.expense_service import ExpenseService
from app.services.issue_service import IssueService
from app.services.payment_service import PaymentService
from app.services.product_service import CategoryService, ProductService
from app.services.sale_service import SaleService
from app.services.scoring_service import ScoringService
from app.services.stock_purchase_service import StockPurchaseService
from app.services.supplier_service import SupplierService
from app.services.sync_service import SyncService


def get_customer_service(ctx: BusinessCtx) -> CustomerService:
    return CustomerService(ctx)


def get_supplier_service(ctx: BusinessCtx) -> SupplierService:
    return SupplierService(ctx)


def get_category_service(ctx: BusinessCtx) -> CategoryService:
    return CategoryService(ctx)


def get_product_service(ctx: BusinessCtx) -> ProductService:
    return ProductService(ctx)


def get_sale_service(ctx: BusinessCtx) -> SaleService:
    return SaleService(ctx)


def get_payment_service(ctx: BusinessCtx) -> PaymentService:
    return PaymentService(ctx)


def get_expense_service(ctx: BusinessCtx) -> ExpenseService:
    return ExpenseService(ctx)


def get_stock_purchase_service(ctx: BusinessCtx) -> StockPurchaseService:
    return StockPurchaseService(ctx)


def get_capital_service(ctx: BusinessCtx) -> CapitalService:
    return CapitalService(ctx)


def get_dashboard_service(ctx: BusinessCtx) -> DashboardService:
    return DashboardService(ctx)


def get_correction_service(ctx: BusinessCtx) -> CorrectionService:
    return CorrectionService(ctx)


def get_issue_service(ctx: BusinessCtx) -> IssueService:
    return IssueService(ctx)


def get_sync_service(ctx: BusinessCtx) -> SyncService:
    return SyncService(ctx)


def get_scoring_service(ctx: BusinessCtx) -> ScoringService:
    return ScoringService(ctx)


CustomerServiceDep = Annotated[CustomerService, Depends(get_customer_service)]
SupplierServiceDep = Annotated[SupplierService, Depends(get_supplier_service)]
CategoryServiceDep = Annotated[CategoryService, Depends(get_category_service)]
ProductServiceDep = Annotated[ProductService, Depends(get_product_service)]
SaleServiceDep = Annotated[SaleService, Depends(get_sale_service)]
PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
ExpenseServiceDep = Annotated[ExpenseService, Depends(get_expense_service)]
StockPurchaseServiceDep = Annotated[
    StockPurchaseService, Depends(get_stock_purchase_service)
]
CapitalServiceDep = Annotated[CapitalService, Depends(get_capital_service)]
DashboardServiceDep = Annotated[DashboardService, Depends(get_dashboard_service)]
CorrectionServiceDep = Annotated[CorrectionService, Depends(get_correction_service)]
IssueServiceDep = Annotated[IssueService, Depends(get_issue_service)]
SyncServiceDep = Annotated[SyncService, Depends(get_sync_service)]
ScoringServiceDep = Annotated[ScoringService, Depends(get_scoring_service)]
