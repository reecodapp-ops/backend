"""Device model — mirrors the ``devices`` table in dukamate_schema_v3.sql exactly.

Schema reference:
    id                 UUID PK DEFAULT gen_random_uuid()
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
    device_fingerprint VARCHAR(255) NOT NULL
    platform           VARCHAR(20) NOT NULL CHECK (platform IN ('ANDROID','IOS'))
    app_version        VARCHAR(20)
    last_seen_at       TIMESTAMPTZ
    is_active          BOOLEAN NOT NULL DEFAULT TRUE
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()

Used for shared-device security: explicit logout deactivates the device row.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_column, uuid_pk


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('ANDROID', 'IOS')", name="devices_platform_check"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="devices")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Device id={self.id} user={self.user_id} platform={self.platform}>"
