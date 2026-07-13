"""Product category and product models — mirror dukamate_schema_v3.sql.

product_categories:
  - SMALLSERIAL PK
  - business_id NULL = system default (shared by all businesses)
  - UNIQUE (business_id, name)

products:
  - stock_on_hand / avg_buying_cost are trigger-maintained aggregates; the
    master-data module sets initial stock directly on create (per blueprint),
    and never rewrites historical sale_item cost snapshots on price edits.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    created_at_column,
    updated_at_column,
    uuid_pk,
)

# Category/lookup PKs are SMALLSERIAL in PostgreSQL. To keep the SQLite test
# database autoincrementing the same way, use an Integer rowid alias on SQLite
# and SMALLINT on PostgreSQL.
_SMALL_PK = Integer().with_variant(SmallInteger(), "postgresql")


class ProductCategory(Base):
    __tablename__ = "product_categories"
    __table_args__ = (
        UniqueConstraint("business_id", "name", name="uq_product_categories_biz_name"),
    )

    id: Mapped[int] = mapped_column(_SMALL_PK, primary_key=True, autoincrement=True)
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icon_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    color_hex: Mapped[str | None] = mapped_column(String(7), nullable=True)
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE"), default=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"), default=True
    )
    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProductCategory id={self.id} name={self.name!r}>"


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')", name="products_status_check"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    category_id: Mapped[int | None] = mapped_column(
        SmallInteger,
        ForeignKey("product_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    selling_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    buying_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    stock_on_hand: Mapped[Decimal] = mapped_column(
        Numeric(15, 4), nullable=False, server_default=text("0"), default=Decimal("0")
    )
    low_stock_level: Mapped[Decimal] = mapped_column(
        Numeric(15, 4), nullable=False, server_default=text("5"), default=Decimal("5")
    )
    avg_buying_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_thumbnail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'"), default="ACTIVE"
    )
    last_sold_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_restocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Product id={self.id} name={self.name!r}>"
