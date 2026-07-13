"""User model — email-based auth flow.

Schema:
    id                  UUID PK
    email               VARCHAR(255) NOT NULL UNIQUE
    phone_number        VARCHAR(20) NULL  (kept for future SMS / contact use)
    password_hash       VARCHAR(255) NULL (NULL between register and set-password)
    full_name           VARCHAR(150) NOT NULL
    is_email_verified   BOOLEAN NOT NULL DEFAULT FALSE
    is_phone_verified   BOOLEAN NOT NULL DEFAULT FALSE
    is_active           BOOLEAN NOT NULL DEFAULT TRUE
    last_login_at       TIMESTAMPTZ NULL

password_hash is nullable: a user is created at registration time with no
password and only sets one after email verification succeeds. The set-password
endpoint is the only writer; login refuses any account whose password is not
yet set.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    created_at_column,
    updated_at_column,
    uuid_pk,
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_phone_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    devices: Mapped[list["Device"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="noload",
    )
    verification_codes: Mapped[list["VerificationCode"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email}>"
