"""Authentication tests — email + OTP flow.

Covers: register → verify-email → set-password → login, resend cooldown,
forgot-password / reset-password, validation failures, refresh, /me, logout.
The EmailService runs in log-only mode (set in conftest), so OTPs are read
straight from the DB via the `_latest_otp_for` helper.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    _latest_otp_for,
    auth_headers,
    login_payload,
    register_and_login,
    register_payload,
)

API = "/api/v1/auth"
pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Full flow: register -> verify -> set-password -> login -> refresh -> me
# --------------------------------------------------------------------------- #
async def test_full_register_flow(client: AsyncClient, session_factory):
    email = "alice@example.com"

    # 1. register: creates an unverified user, sends OTP. No tokens yet.
    r = await client.post(f"{API}/register", json=register_payload(email=email))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["email"] == email
    assert body["user"]["is_email_verified"] is False
    assert "access_token" not in body  # no tokens at this step

    # 2. verify-email
    code = await _latest_otp_for(session_factory, email, "EMAIL_VERIFICATION")
    r = await client.post(f"{API}/verify-email", json={"email": email, "code": code})
    assert r.status_code == 200
    assert r.json()["user"]["is_email_verified"] is True

    # 3. set-password — issues the first token pair
    r = await client.post(
        f"{API}/set-password", json={"email": email, "password": "StrongPass123"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    access = body["access_token"]
    refresh = body["refresh_token"]

    # 4. login as the same user (independent of set-password)
    r = await client.post(f"{API}/login", json=login_payload(email=email))
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    assert r.json()["user"]["email"] == email

    # 5. /me
    r = await client.get(f"{API}/me", headers=auth_headers(access))
    assert r.status_code == 200
    assert r.json()["email"] == email

    # 6. refresh
    r = await client.post(f"{API}/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200
    assert r.json()["access_token"]


# --------------------------------------------------------------------------- #
# Verify-email
# --------------------------------------------------------------------------- #
async def test_verify_email_wrong_code(client: AsyncClient, session_factory):
    email = "wrong-code@example.com"
    await client.post(f"{API}/register", json=register_payload(email=email))
    r = await client.post(f"{API}/verify-email", json={"email": email, "code": "000000"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_otp"


async def test_verify_email_otp_cannot_be_reused(
    client: AsyncClient, session_factory
):
    email = "reuse@example.com"
    await client.post(f"{API}/register", json=register_payload(email=email))
    code = await _latest_otp_for(session_factory, email, "EMAIL_VERIFICATION")
    r1 = await client.post(f"{API}/verify-email", json={"email": email, "code": code})
    assert r1.status_code == 200
    # second attempt with the same code -> reject
    r2 = await client.post(f"{API}/verify-email", json={"email": email, "code": code})
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "invalid_otp"


# --------------------------------------------------------------------------- #
# Resend OTP
# --------------------------------------------------------------------------- #
async def test_resend_invalidates_previous_code(
    client: AsyncClient, session_factory
):
    """The strong invariant: after a resend, only the most recent code is
    redeemable. We assert this by checking DB state directly (count of
    active codes) rather than relying on OTP values being distinct — two
    randomly-generated 6-digit codes collide ~1 in 10^6 and the test must
    not flake."""
    from sqlalchemy import func, select
    from app.models.user import User
    from app.models.verification_code import VerificationCode

    email = "resend@example.com"
    await client.post(f"{API}/register", json=register_payload(email=email))

    # one active code present after register
    async with session_factory() as s:
        user_id = (
            await s.execute(select(User.id).where(User.email == email))
        ).scalar_one()
        active = (
            await s.execute(
                select(func.count()).select_from(VerificationCode).where(
                    VerificationCode.user_id == user_id,
                    VerificationCode.used_at.is_(None),
                )
            )
        ).scalar_one()
    assert active == 1

    r = await client.post(
        f"{API}/resend-otp",
        json={"email": email, "verification_type": "EMAIL_VERIFICATION"},
    )
    assert r.status_code == 200

    # Exactly one active code after resend (the previous was invalidated).
    async with session_factory() as s:
        active = (
            await s.execute(
                select(func.count()).select_from(VerificationCode).where(
                    VerificationCode.user_id == user_id,
                    VerificationCode.used_at.is_(None),
                )
            )
        ).scalar_one()
        all_codes = (
            await s.execute(
                select(func.count()).select_from(VerificationCode).where(
                    VerificationCode.user_id == user_id
                )
            )
        ).scalar_one()
    assert active == 1
    assert all_codes == 2  # original + the new one (original marked used)

    # The current latest code does verify.
    code = await _latest_otp_for(session_factory, email, "EMAIL_VERIFICATION")
    r = await client.post(f"{API}/verify-email", json={"email": email, "code": code})
    assert r.status_code == 200


async def test_resend_for_already_verified_email_rejected(
    client: AsyncClient, session_factory
):
    email = "alreadyok@example.com"
    await client.post(f"{API}/register", json=register_payload(email=email))
    code = await _latest_otp_for(session_factory, email, "EMAIL_VERIFICATION")
    await client.post(f"{API}/verify-email", json={"email": email, "code": code})
    r = await client.post(
        f"{API}/resend-otp",
        json={"email": email, "verification_type": "EMAIL_VERIFICATION"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "email_already_verified"


async def test_resend_for_unknown_email_silent_for_password_reset(
    client: AsyncClient,
):
    """PASSWORD_RESET resend must not leak whether the email exists."""
    r = await client.post(
        f"{API}/resend-otp",
        json={"email": "nobody@example.com", "verification_type": "PASSWORD_RESET"},
    )
    assert r.status_code == 200


async def test_resend_for_unknown_email_rejects_email_verification(
    client: AsyncClient,
):
    """EMAIL_VERIFICATION resend on an unknown email surfaces a clean error
    (no security trade-off — they haven't registered)."""
    r = await client.post(
        f"{API}/resend-otp",
        json={"email": "nobody2@example.com", "verification_type": "EMAIL_VERIFICATION"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_pending_verification"


# --------------------------------------------------------------------------- #
# Set-password
# --------------------------------------------------------------------------- #
async def test_set_password_requires_verified_email(client: AsyncClient):
    email = "noverify@example.com"
    await client.post(f"{API}/register", json=register_payload(email=email))
    r = await client.post(
        f"{API}/set-password", json={"email": email, "password": "StrongPass123"}
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "email_not_verified"


async def test_set_password_cannot_run_twice(
    client: AsyncClient, session_factory
):
    email = "twicepw@example.com"
    await client.post(f"{API}/register", json=register_payload(email=email))
    code = await _latest_otp_for(session_factory, email, "EMAIL_VERIFICATION")
    await client.post(f"{API}/verify-email", json={"email": email, "code": code})
    r1 = await client.post(
        f"{API}/set-password", json={"email": email, "password": "StrongPass123"}
    )
    assert r1.status_code == 200
    r2 = await client.post(
        f"{API}/set-password", json={"email": email, "password": "Another123"}
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "password_already_set"


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
async def test_login_with_unverified_email_rejected(client: AsyncClient):
    email = "unverif@example.com"
    await client.post(f"{API}/register", json=register_payload(email=email))
    r = await client.post(f"{API}/login", json=login_payload(email=email))
    assert r.status_code in (401, 403)


async def test_login_bad_password(client: AsyncClient, session_factory):
    email = "badpw@example.com"
    await register_and_login(client, session_factory, email=email)
    r = await client.post(
        f"{API}/login", json={"email": email, "password": "WrongOne123"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


async def test_login_unknown_email_uniform_401(client: AsyncClient):
    r = await client.post(
        f"{API}/login", json={"email": "ghost@example.com", "password": "Anything123"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


# --------------------------------------------------------------------------- #
# Forgot / reset password
# --------------------------------------------------------------------------- #
async def test_forgot_password_unknown_email_silent(client: AsyncClient):
    r = await client.post(
        f"{API}/forgot-password", json={"email": "ghost@example.com"}
    )
    assert r.status_code == 200
    assert r.json()["success"] is True


async def test_reset_password_full_flow(client: AsyncClient, session_factory):
    email = "reset@example.com"
    await register_and_login(client, session_factory, email=email)

    r = await client.post(f"{API}/forgot-password", json={"email": email})
    assert r.status_code == 200
    code = await _latest_otp_for(session_factory, email, "PASSWORD_RESET")

    r = await client.post(
        f"{API}/reset-password",
        json={"email": email, "code": code, "new_password": "Brandnew456"},
    )
    assert r.status_code == 200

    # old password no longer works
    r = await client.post(
        f"{API}/login", json={"email": email, "password": "StrongPass123"}
    )
    assert r.status_code == 401
    # new password works
    r = await client.post(
        f"{API}/login", json={"email": email, "password": "Brandnew456"}
    )
    assert r.status_code == 200


async def test_reset_password_invalid_code(client: AsyncClient, session_factory):
    email = "resetbad@example.com"
    await register_and_login(client, session_factory, email=email)
    r = await client.post(
        f"{API}/reset-password",
        json={"email": email, "code": "000000", "new_password": "Newpass1234"},
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Register validation + duplicate
# --------------------------------------------------------------------------- #
async def test_register_duplicate_verified_email_blocked(
    client: AsyncClient, session_factory
):
    email = "dup@example.com"
    await register_and_login(client, session_factory, email=email)
    # re-register with a fully-verified account -> 409
    r = await client.post(f"{API}/register", json=register_payload(email=email))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "email_taken"


async def test_register_resends_otp_when_account_is_unverified(client: AsyncClient):
    email = "redo@example.com"
    r1 = await client.post(f"{API}/register", json=register_payload(email=email))
    assert r1.status_code == 201
    r2 = await client.post(f"{API}/register", json=register_payload(email=email))
    # Same email, still unverified -> we accept and re-send OTP rather than 409.
    assert r2.status_code == 201


async def test_register_bad_email_rejected(client: AsyncClient):
    r = await client.post(
        f"{API}/register",
        json={"full_name": "Test", "email": "not-an-email"},
    )
    assert r.status_code == 422


async def test_register_empty_name_rejected(client: AsyncClient):
    r = await client.post(
        f"{API}/register", json={"full_name": "  ", "email": "name@example.com"}
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
async def test_protected_route_without_token(client: AsyncClient):
    r = await client.get(f"{API}/me")
    assert r.status_code == 401


async def test_refresh_with_invalid_token(client: AsyncClient):
    r = await client.post(f"{API}/refresh", json={"refresh_token": "not.a.real.token"})
    assert r.status_code == 401


async def test_logout_stateless(client: AsyncClient, session_factory):
    session = await register_and_login(client, session_factory, email="lout@example.com")
    r = await client.post(
        f"{API}/logout", headers=auth_headers(session["access_token"])
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
