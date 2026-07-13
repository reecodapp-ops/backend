"""Pydantic v2 request/response schemas for the email-OTP auth flow.

Endpoints covered:
  POST /auth/register
  POST /auth/verify-email
  POST /auth/resend-otp
  POST /auth/set-password
  POST /auth/login
  POST /auth/forgot-password
  POST /auth/reset-password
  GET  /auth/me
  POST /auth/refresh
  POST /auth/logout

Verification OTPs are always exactly the configured length and digits-only at
the schema layer; the service layer enforces freshness/expiry/single-use.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

OTP_RE = re.compile(r"^\d+$")

# Verification_type values accepted by /auth/resend-otp. EMAIL_CHANGE is
# excluded — that flow is triggered server-side from the (future) email-change
# endpoint, not directly by the client.
ResendType = Literal["EMAIL_VERIFICATION", "PASSWORD_RESET"]


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=150)
    email: EmailStr = Field(..., max_length=255)

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("full_name cannot be empty")
        return v


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=10)

    @field_validator("code")
    @classmethod
    def _digits(cls, v: str) -> str:
        v = v.strip()
        if not OTP_RE.match(v):
            raise ValueError("code must contain digits only")
        return v


class ResendOtpRequest(BaseModel):
    email: EmailStr
    verification_type: ResendType


class SetPasswordRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=10)
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("code")
    @classmethod
    def _digits(cls, v: str) -> str:
        v = v.strip()
        if not OTP_RE.match(v):
            raise ValueError("code must contain digits only")
        return v


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    phone_number: str | None
    full_name: str
    is_email_verified: bool
    is_phone_verified: bool
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class BusinessSummary(BaseModel):
    """Lightweight business reference returned with auth responses so the
    client can route immediately to the dashboard (if the user has at least
    one business) or to the onboarding flow (if the list is empty). Currency
    is included because the app shell typically displays it in the header
    next to the shop name — without it the client would have to make a second
    GET /businesses/{id} call just to render the dashboard frame."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shop_name: str
    currency_code: str
    role: str = "owner"


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RegisterResponse(BaseModel):
    """Registration does NOT issue tokens — the user must verify email and
    set a password first."""

    user: UserOut
    message: str = (
        "Verification code sent. Check your email and call /auth/verify-email."
    )


class VerifyEmailResponse(BaseModel):
    user: UserOut
    message: str = "Email verified. You can now set your password."


class SetPasswordResponse(BaseModel):
    user: UserOut
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # Populated for completeness; new accounts will always be []. Included so
    # the client doesn't need to branch on auth response shape.
    businesses: list[BusinessSummary] = Field(default_factory=list)
    has_business: bool = False


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut
    businesses: list[BusinessSummary] = Field(default_factory=list)
    # Convenience flag the client can route on without computing
    # len(businesses) > 0 itself. The data is redundant with `businesses`
    # but having it explicit removes ambiguity in the three or four
    # places the client makes routing decisions.
    has_business: bool = False


class MeResponse(BaseModel):
    """The "bootstrap the app" endpoint. Returns the current user plus enough
    business context for the client to decide whether to show the dashboard
    or the onboarding flow, without a follow-up call. Distinct from
    ``UserOut`` because that schema is also nested inside login/set-password
    responses where the businesses list would be duplicated."""

    user: UserOut
    businesses: list[BusinessSummary] = Field(default_factory=list)
    has_business: bool = False


class MessageResponse(BaseModel):
    success: bool = True
    message: str | None = None


__all__ = [
    "RegisterRequest",
    "VerifyEmailRequest",
    "ResendOtpRequest",
    "SetPasswordRequest",
    "LoginRequest",
    "ForgotPasswordRequest",
    "ResetPasswordRequest",
    "RefreshRequest",
    "UserOut",
    "BusinessSummary",
    "TokenPair",
    "AccessToken",
    "RegisterResponse",
    "VerifyEmailResponse",
    "SetPasswordResponse",
    "LoginResponse",
    "MeResponse",
    "MessageResponse",
    "ResendType",
]
