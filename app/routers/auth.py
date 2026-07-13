"""Authentication router — email + OTP flow.

Endpoints:
    POST /auth/register             create unverified user, send email OTP
    POST /auth/verify-email         consume OTP, mark email verified
    POST /auth/resend-otp           re-issue OTP (rate-limited)
    POST /auth/set-password         set password after verification -> tokens
    POST /auth/login                email + password -> tokens
    POST /auth/forgot-password      issue PASSWORD_RESET OTP (silent if unknown)
    POST /auth/reset-password       consume OTP, set new password
    POST /auth/refresh              refresh access token
    POST /auth/logout               logout (stateless; for client UX symmetry)
    GET  /auth/me                   current authenticated user
"""
from __future__ import annotations

from fastapi import APIRouter, status

from app.core.dependencies import AuthServiceDep, CurrentUser
from app.schemas.auth import (
    AccessToken,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    ResendOtpRequest,
    ResetPasswordRequest,
    SetPasswordRequest,
    SetPasswordResponse,
    UserOut,
    VerifyEmailRequest,
    VerifyEmailResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an unverified user and send an email verification OTP",
)
async def register(
    payload: RegisterRequest, service: AuthServiceDep
) -> RegisterResponse:
    return await service.register(payload)


@router.post(
    "/verify-email",
    response_model=VerifyEmailResponse,
    summary="Verify the OTP that was sent to the user's email",
)
async def verify_email(
    payload: VerifyEmailRequest, service: AuthServiceDep
) -> VerifyEmailResponse:
    return await service.verify_email(payload)


@router.post(
    "/resend-otp",
    response_model=MessageResponse,
    summary="Re-issue an OTP (rate-limited to one per cooldown window)",
)
async def resend_otp(
    payload: ResendOtpRequest, service: AuthServiceDep
) -> MessageResponse:
    return await service.resend_otp(payload)


@router.post(
    "/set-password",
    response_model=SetPasswordResponse,
    summary="Set the user's password after email verification",
)
async def set_password(
    payload: SetPasswordRequest, service: AuthServiceDep
) -> SetPasswordResponse:
    return await service.set_password(payload)


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate with email + password",
)
async def login(payload: LoginRequest, service: AuthServiceDep) -> LoginResponse:
    return await service.login(payload)


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="Send a password reset OTP (does not reveal whether the email exists)",
)
async def forgot_password(
    payload: ForgotPasswordRequest, service: AuthServiceDep
) -> MessageResponse:
    return await service.forgot_password(payload)


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Consume a password reset OTP and set a new password",
)
async def reset_password(
    payload: ResetPasswordRequest, service: AuthServiceDep
) -> MessageResponse:
    return await service.reset_password(payload)


@router.post(
    "/refresh",
    response_model=AccessToken,
    summary="Exchange a refresh token for a new access token",
)
async def refresh(payload: RefreshRequest, service: AuthServiceDep) -> AccessToken:
    return await service.refresh(payload)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Log out (stateless: client should discard tokens)",
)
async def logout(
    service: AuthServiceDep, current_user: CurrentUser
) -> MessageResponse:
    return await service.logout(current_user)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Get the current user plus their businesses (for client routing)",
)
async def me(service: AuthServiceDep, current_user: CurrentUser) -> MeResponse:
    return await service.me(current_user)
