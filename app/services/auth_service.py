"""Authentication service — email + OTP flow.

The flow has seven user-facing endpoints:

  1. POST /auth/register       -> create unverified user, issue OTP
  2. POST /auth/verify-email   -> verify OTP, mark email verified
  3. POST /auth/resend-otp     -> re-issue OTP (rate limited)
  4. POST /auth/set-password   -> set password, issue tokens
  5. POST /auth/login          -> email + password -> tokens
  6. POST /auth/forgot-password -> issue PASSWORD_RESET OTP (silent if unknown)
  7. POST /auth/reset-password -> consume OTP, set new password

Plus internal endpoints kept from the prior version: /auth/refresh, /auth/me,
/auth/logout. The logout endpoint no longer manipulates device rows since the
new register/login flow does not capture devices; sync_events keeps its
device_id column for clients that send one through /sync.

Privacy:
- forgot-password returns the same response whether the email exists or not, to
  avoid leaking which addresses are registered.
- verify and login use generic error codes for the same reason.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.email_service import EmailService
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    UnauthorizedError,
    ValidationError,
)
from app.core.otp_utils import generate_otp, is_expired, otp_expires_at
from app.core.security import (
    REFRESH_TOKEN_TYPE,
    JWTError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.models.verification_code import (
    VERIFICATION_TYPE_EMAIL,
    VERIFICATION_TYPE_PASSWORD_RESET,
)
from app.repositories.business_repository import BusinessRepository
from app.repositories.user_repository import UserRepository
from app.repositories.verification_code_repository import (
    VerificationCodeRepository,
)
from app.schemas.auth import (
    AccessToken,
    BusinessSummary,
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


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        email_service: EmailService | None = None,
    ) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._codes = VerificationCodeRepository(session)
        self._businesses = BusinessRepository(session)
        self._email = email_service or EmailService()

    async def _business_summaries(
        self, user_id: uuid.UUID
    ) -> list[BusinessSummary]:
        """Fetch the user's businesses for inclusion in auth responses.

        Returned with each successful login / set-password / me call so the
        client can route immediately to the dashboard (if non-empty) or to
        the onboarding flow (if empty). Owner is the only role we model
        today; that's hard-coded in BusinessSummary's default.
        """
        rows = await self._businesses.list_for_owner(user_id)
        return [BusinessSummary.model_validate(b) for b in rows]

    # ----- register --------------------------------------------------------- #
    async def register(self, data: RegisterRequest) -> RegisterResponse:
        existing = await self._users.get_by_email(data.email)
        if existing is not None:
            # Distinguish three cases:
            #   (a) verified + has password -> account exists, suggest login
            #   (b) verified, no password   -> let them continue to set-password
            #   (c) unverified              -> let them request a new OTP via resend
            # Returning ConflictError for (a) only avoids letting a fresh user
            # take over a stale unverified record.
            if existing.is_email_verified and existing.password_hash:
                raise ConflictError(
                    "An account with this email already exists. Please log in.",
                    code="email_taken",
                )
            # For (b) and (c) we re-use the existing row and re-send a code.
            user = existing
        else:
            user = await self._users.create_unverified(
                email=data.email, full_name=data.full_name
            )

        await self._issue_otp_and_send(
            user=user, verification_type=VERIFICATION_TYPE_EMAIL
        )
        return RegisterResponse(user=UserOut.model_validate(user))

    # ----- verify-email ----------------------------------------------------- #
    async def verify_email(self, data: VerifyEmailRequest) -> VerifyEmailResponse:
        user = await self._users.get_by_email(data.email)
        if user is None:
            raise ValidationError(
                "Invalid verification code or email.", code="invalid_otp"
            )
        vc = await self._codes.find_active_match(
            user_id=user.id,
            verification_type=VERIFICATION_TYPE_EMAIL,
            code=data.code,
        )
        if vc is None:
            raise ValidationError(
                "Invalid verification code or email.", code="invalid_otp"
            )
        await self._codes.mark_used(vc)
        if not user.is_email_verified:
            await self._users.mark_email_verified(user)
        return VerifyEmailResponse(user=UserOut.model_validate(user))

    # ----- resend-otp ------------------------------------------------------- #
    async def resend_otp(self, data: ResendOtpRequest) -> MessageResponse:
        """Re-issue an OTP if no active code was sent inside the cooldown
        window. Returns a uniform success message even if the user doesn't
        exist (PASSWORD_RESET branch) to avoid enumeration."""
        user = await self._users.get_by_email(data.email)

        if data.verification_type == VERIFICATION_TYPE_EMAIL:
            # Email verification resend only makes sense for a known
            # unverified user. If the user doesn't exist or is already
            # verified, surface a clean error.
            if user is None:
                raise ValidationError(
                    "No pending verification for this email.",
                    code="no_pending_verification",
                )
            if user.is_email_verified:
                raise ValidationError(
                    "This email is already verified.",
                    code="email_already_verified",
                )

        # PASSWORD_RESET: silent if the user doesn't exist.
        if user is None:
            return MessageResponse(success=True, message="If that account exists, a code has been sent.")

        await self._enforce_cooldown(user.id, data.verification_type)
        await self._issue_otp_and_send(
            user=user, verification_type=data.verification_type
        )
        return MessageResponse(success=True, message="Verification code sent.")

    # ----- set-password ----------------------------------------------------- #
    async def set_password(self, data: SetPasswordRequest) -> SetPasswordResponse:
        user = await self._users.get_by_email(data.email)
        if user is None or not user.is_email_verified:
            raise ForbiddenError(
                "Email must be verified before setting a password.",
                code="email_not_verified",
            )
        if user.password_hash is not None:
            raise ConflictError(
                "A password is already set for this account. Use the password "
                "reset flow to change it.",
                code="password_already_set",
            )
        await self._users.set_password(user, hash_password(data.password))
        await self._users.touch_last_login(user)
        subject = str(user.id)
        # New users won't have a business yet, but populate the list anyway
        # so the response shape is identical to /auth/login. Clients can rely
        # on `has_business` to route without inspecting the list length.
        businesses = await self._business_summaries(user.id)
        return SetPasswordResponse(
            user=UserOut.model_validate(user),
            access_token=create_access_token(subject),
            refresh_token=create_refresh_token(subject),
            businesses=businesses,
            has_business=bool(businesses),
        )

    # ----- login ------------------------------------------------------------ #
    async def login(self, data: LoginRequest) -> LoginResponse:
        user = await self._users.get_by_email(data.email)
        # Uniform message for non-existent vs wrong-password vs no-password set.
        if (
            user is None
            or user.password_hash is None
            or not verify_password(data.password, user.password_hash)
        ):
            raise UnauthorizedError(
                "Invalid email or password.", code="invalid_credentials"
            )
        if not user.is_active:
            raise ForbiddenError("This account is inactive.", code="inactive_account")
        if not user.is_email_verified:
            raise ForbiddenError(
                "Please verify your email before logging in.",
                code="email_not_verified",
            )
        await self._users.touch_last_login(user)
        subject = str(user.id)
        businesses = await self._business_summaries(user.id)
        return LoginResponse(
            access_token=create_access_token(subject),
            refresh_token=create_refresh_token(subject),
            user=UserOut.model_validate(user),
            businesses=businesses,
            has_business=bool(businesses),
        )

    # ----- forgot-password ------------------------------------------------- #
    async def forgot_password(
        self, data: ForgotPasswordRequest
    ) -> MessageResponse:
        """Always returns the same response so attackers cannot use this
        endpoint to enumerate registered emails."""
        user = await self._users.get_by_email(data.email)
        message = MessageResponse(
            success=True,
            message="If an account exists for that email, a reset code has been sent.",
        )
        if user is None or not user.is_email_verified:
            return message
        try:
            await self._enforce_cooldown(
                user.id, VERIFICATION_TYPE_PASSWORD_RESET, silent=True
            )
        except ValidationError:
            # Rate-limited; still return the same message (don't leak state).
            return message
        await self._issue_otp_and_send(
            user=user, verification_type=VERIFICATION_TYPE_PASSWORD_RESET
        )
        return message

    # ----- reset-password -------------------------------------------------- #
    async def reset_password(
        self, data: ResetPasswordRequest
    ) -> MessageResponse:
        user = await self._users.get_by_email(data.email)
        if user is None:
            raise ValidationError(
                "Invalid reset code or email.", code="invalid_otp"
            )
        vc = await self._codes.find_active_match(
            user_id=user.id,
            verification_type=VERIFICATION_TYPE_PASSWORD_RESET,
            code=data.code,
        )
        if vc is None:
            raise ValidationError(
                "Invalid reset code or email.", code="invalid_otp"
            )
        await self._codes.mark_used(vc)
        await self._users.set_password(user, hash_password(data.new_password))
        return MessageResponse(
            success=True, message="Password reset. You can now log in."
        )

    # ----- refresh / me / logout ------------------------------------------- #
    async def refresh(self, data: RefreshRequest) -> AccessToken:
        try:
            claims = decode_token(data.refresh_token)
        except JWTError as exc:
            raise UnauthorizedError(
                "Invalid or expired refresh token.", code="invalid_token"
            ) from exc
        if claims.get("type") != REFRESH_TOKEN_TYPE:
            raise UnauthorizedError(
                "Provided token is not a refresh token.", code="wrong_token_type"
            )
        subject = claims.get("sub")
        if not subject:
            raise UnauthorizedError("Malformed token.", code="invalid_token")
        user = await self._users.get_by_id(uuid.UUID(subject))
        if user is None or not user.is_active:
            raise UnauthorizedError("User no longer valid.", code="invalid_token")
        return AccessToken(access_token=create_access_token(subject))

    async def me(self, user: User) -> MeResponse:
        """Bootstrap endpoint: returns the current user plus business context
        so the client can route to the dashboard or onboarding without a
        follow-up call. Distinct from the UserOut nested inside login /
        set-password responses to avoid duplicating the businesses list."""
        businesses = await self._business_summaries(user.id)
        return MeResponse(
            user=UserOut.model_validate(user),
            businesses=businesses,
            has_business=bool(businesses),
        )

    async def logout(self, user: User) -> MessageResponse:
        # Stateless JWTs: logout is a client-side discard. A real token-store
        # implementation would revoke the refresh token here.
        return MessageResponse(success=True, message="Logged out.")

    # ----- internals ------------------------------------------------------- #
    async def _issue_otp_and_send(
        self, *, user: User, verification_type: str
    ) -> None:
        # Invalidate any previous active codes for the same (user, type) so
        # only the latest OTP is valid.
        await self._codes.invalidate_active(
            user_id=user.id, verification_type=verification_type
        )
        code = generate_otp()
        await self._codes.insert(
            user_id=user.id,
            code=code,
            verification_type=verification_type,
            expires_at=otp_expires_at(),
        )
        if verification_type == VERIFICATION_TYPE_EMAIL:
            await self._email.send_email_verification(
                to_address=user.email, full_name=user.full_name, code=code
            )
        elif verification_type == VERIFICATION_TYPE_PASSWORD_RESET:
            await self._email.send_password_reset(
                to_address=user.email, full_name=user.full_name, code=code
            )
        else:  # EMAIL_CHANGE
            await self._email.send_email_change(
                to_address=user.email, full_name=user.full_name, code=code
            )

    async def _enforce_cooldown(
        self,
        user_id: uuid.UUID,
        verification_type: str,
        *,
        silent: bool = False,
    ) -> None:
        """Reject if the most recent OTP for (user, type) was sent inside the
        configured cooldown window. ``silent=True`` lets the caller decide
        whether to return a generic success instead of a 429."""
        latest = await self._codes.get_latest_for(user_id, verification_type)
        if latest is None:
            return
        elapsed = (
            datetime.now(timezone.utc) - latest.created_at.replace(
                tzinfo=latest.created_at.tzinfo or timezone.utc
            )
        ).total_seconds()
        if elapsed < settings.otp_resend_cooldown_seconds:
            retry_after = int(settings.otp_resend_cooldown_seconds - elapsed)
            raise ValidationError(
                f"Please wait {retry_after} seconds before requesting another code.",
                code="resend_too_soon",
            )
