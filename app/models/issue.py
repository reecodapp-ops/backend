"""Issue / Decision Engine models — mirror dukamate_schema_v3.sql section 6.

Issues are guidance cards written by the Decision Engine worker. They're
persisted (not computed on read) so the dashboard's "Issue Cards" can render
without re-running rules. confidence_level gates which cards surface to which
plan tier. data_snapshot captures the numbers that triggered the issue so the
UI doesn't need to re-query when rendering.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    created_at_column,
    updated_at_column,
    uuid_pk,
)

_SMALL_PK = Integer().with_variant(SmallInteger(), "postgresql")
# JSONB on PostgreSQL, plain JSON on SQLite for portability in tests.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class IssueType(Base):
    """Seeded lookup. Engine writes rows in ``issues`` referencing these codes."""

    __tablename__ = "issue_types"
    __table_args__ = (
        CheckConstraint(
            "severity_default IN ('INFO','WARN','ALERT')",
            name="issue_types_severity_check",
        ),
    )

    id: Mapped[int] = mapped_column(_SMALL_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    engine_phase: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    severity_default: Mapped[str] = mapped_column(
        String(10), nullable=False, default="INFO"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        CheckConstraint(
            "confidence_level BETWEEN 1 AND 4", name="issues_confidence_check"
        ),
        CheckConstraint(
            "severity IN ('INFO','WARN','ALERT')", name="issues_severity_check"
        ),
        CheckConstraint(
            "status IN ('ACTIVE','RESOLVED','IGNORED','SUPERSEDED')",
            name="issues_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    issue_type_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("issue_types.id"), nullable=False
    )
    ref_product_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=True,
    )
    ref_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=True,
    )
    ref_sale_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sales.id", ondelete="CASCADE"),
        nullable=True,
    )
    confidence_level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1"), default=1
    )
    severity: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'INFO'"), default="INFO"
    )
    headline: Mapped[str] = mapped_column(String(300), nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(String(300), nullable=True)
    data_snapshot: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'"), default="ACTIVE"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
