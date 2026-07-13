"""Repository layer — the only layer that builds database queries."""
from app.repositories.business_repository import BusinessRepository
from app.repositories.customer_repository import CustomerRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.product_repository import (
    CategoryRepository,
    ProductRepository,
)
from app.repositories.capital_repository import CapitalRepository
from app.repositories.correction_repository import CorrectionRepository
from app.repositories.dashboard_repository import DashboardRepository
from app.repositories.issue_repository import IssueRepository
from app.repositories.insights_repository import InsightsRepository
from app.repositories.scoring_repository import ScoringRepository
from app.repositories.sync_repository import SyncRepository
from app.repositories.expense_repository import ExpenseRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.stock_purchase_repository import StockPurchaseRepository
from app.repositories.sale_repository import SaleRepository
from app.repositories.supplier_repository import SupplierRepository
from app.repositories.user_repository import UserRepository
from app.repositories.verification_code_repository import VerificationCodeRepository

__all__ = [
    "UserRepository",
    "DeviceRepository",
    "BusinessRepository",
    "CustomerRepository",
    "SupplierRepository",
    "CategoryRepository",
    "ProductRepository",
    "SaleRepository",
    "PaymentRepository",
    "ExpenseRepository",
    "StockPurchaseRepository",
    "CapitalRepository",
    "DashboardRepository",
    "CorrectionRepository",
    "IssueRepository",
    "InsightsRepository",
    "ScoringRepository",
    "SyncRepository",
    "VerificationCodeRepository",
]
