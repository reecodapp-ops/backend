"""Application configuration loaded from environment variables.

All settings are read once at startup via a cached Settings instance. Secrets
(JWT signing key, DB URL, SMTP password) must be provided through the
environment in production; the defaults here are development-only conveniences.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- General -----
    app_name: str = "DukaMate API"
    api_v1_prefix: str = "/api/v1"
    environment: str = Field(default="development")
    debug: bool = Field(default=True)

    # ----- Database -----
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/dukamate"
    )
    db_echo: bool = Field(default=False)
    db_pool_size: int = Field(default=10)
    db_max_overflow: int = Field(default=20)

    # ----- Security / JWT -----
    jwt_secret_key: str = Field(default="CHANGE_ME_IN_PRODUCTION_dev_only_secret_key")
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=15)
    refresh_token_expire_days: int = Field(default=30)

    # ----- Password hashing -----
    bcrypt_rounds: int = Field(default=12)

    # ----- OTP / verification ----------------------------------------------- #
    otp_length: int = Field(default=6, ge=4, le=10)
    otp_ttl_minutes: int = Field(default=10, description="How long an OTP stays valid.")
    otp_resend_cooldown_seconds: int = Field(
        default=60,
        description=(
            "Minimum seconds between two OTP issuances for the same "
            "(user, verification_type). Resend requests during the cooldown "
            "are rejected with 429."
        ),
    )

    # ----- Email / SMTP ----------------------------------------------------- #
    email_provider: str = Field(
        default="smtp",
        description=(
            "Pluggable provider name. 'smtp' is the default; flip to "
            "'sendgrid', 'ses', 'mailgun', 'resend' etc. by extending "
            "EmailService._build_sender()."
        ),
    )
    email_from_address: str = Field(default="no-reply@dukamate.app")
    email_from_name: str = Field(default="DukaMate")
    email_log_only: bool = Field(
        default=False,
        description=(
            "Skip actual SMTP delivery and log the rendered email instead. "
            "Set to TRUE in tests and local dev; FALSE in production."
        ),
    )
    smtp_host: str = Field(default="smtp.gmail.com")
    smtp_port: int = Field(default=587)
    smtp_username: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_use_tls: bool = Field(
        default=True,
        description="STARTTLS on port 587. Set false + smtp_use_ssl true for port 465.",
    )
    smtp_use_ssl: bool = Field(
        default=False,
        description="Implicit TLS (SMTPS). Use with port 465 when smtp_use_tls is false.",
    )
    smtp_timeout_seconds: int = Field(default=30)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read environment only once)."""
    return Settings()


settings = get_settings()
