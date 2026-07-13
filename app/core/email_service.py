"""EmailService — asynchronous transactional email.

The sender is pluggable: business logic calls EmailService methods
(``send_email_verification``, ``send_password_reset``, ``send_email_change``)
and the service builds the right HTML+subject from a template, then delegates
delivery to a small ``_EmailSender`` strategy that can be swapped per
``email_provider`` setting.

Switching providers (SendGrid, SES, Mailgun, Resend, Postmark, etc.) means
adding one ``_EmailSender`` implementation and wiring it in
``_build_sender()``. The methods on EmailService stay identical, so services
calling EmailService do not need to change.

Templates live in app/core/email_templates/*.html and use Python str.format()
so we have no hard runtime dependency on Jinja2.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from app.core.config import Settings, settings

LOG = logging.getLogger("dukamate.email")

_TEMPLATES_DIR = Path(__file__).resolve().parent / "email_templates"


# --------------------------------------------------------------------------- #
# Sender interface (provider-agnostic)
# --------------------------------------------------------------------------- #
@dataclass
class OutgoingEmail:
    to_address: str
    to_name: str | None
    subject: str
    html: str
    text: str


class _EmailSender:
    """Strategy interface. Implementations must be coroutine-friendly."""

    async def send(self, msg: OutgoingEmail) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class _LogOnlySender(_EmailSender):
    """Used in tests / local dev: just logs what would have been sent."""

    async def send(self, msg: OutgoingEmail) -> None:
        LOG.info(
            "[email-log-only] to=%s subject=%r preview=%r",
            msg.to_address,
            msg.subject,
            msg.text[:160].replace("\n", " "),
        )


class _SMTPSender(_EmailSender):
    """SMTP sender using aiosmtplib.

    Imported lazily so SMTP isn't a hard install dependency when the
    log-only sender is used (e.g. in CI / unit tests).
    """

    def __init__(self, s: Settings) -> None:
        self._cfg = s

    async def send(self, msg: OutgoingEmail) -> None:
        try:
            import aiosmtplib  # local import to keep test envs light
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "aiosmtplib is not installed. Either `pip install aiosmtplib` "
                "or set EMAIL_LOG_ONLY=true."
            ) from exc

        message = EmailMessage()
        message["From"] = formataddr(
            (self._cfg.email_from_name, self._cfg.email_from_address)
        )
        message["To"] = (
            formataddr((msg.to_name, msg.to_address)) if msg.to_name else msg.to_address
        )
        message["Subject"] = msg.subject
        message.set_content(msg.text)
        message.add_alternative(msg.html, subtype="html")

        await aiosmtplib.send(
            message,
            hostname=self._cfg.smtp_host,
            port=self._cfg.smtp_port,
            start_tls=self._cfg.smtp_use_tls and not self._cfg.smtp_use_ssl,
            use_tls=self._cfg.smtp_use_ssl,
            username=self._cfg.smtp_username or None,
            password=self._cfg.smtp_password or None,
            timeout=self._cfg.smtp_timeout_seconds,
        )


def _build_sender(s: Settings) -> _EmailSender:
    """Return the configured sender. Extend here to add provider strategies.

    Example future extension::

        if s.email_provider == "sendgrid":
            return _SendGridSender(s)
        if s.email_provider == "ses":
            return _SESSender(s)
        if s.email_provider == "mailgun":
            return _MailgunSender(s)
        if s.email_provider == "resend":
            return _ResendSender(s)
    """
    if s.email_log_only:
        return _LogOnlySender()
    provider = (s.email_provider or "smtp").lower()
    if provider == "smtp":
        return _SMTPSender(s)
    raise ValueError(
        f"Unknown email_provider '{provider}'. "
        "Extend _build_sender() in app/core/email_service.py."
    )


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def _load_template(name: str) -> str:
    path = _TEMPLATES_DIR / name
    return path.read_text(encoding="utf-8")


def _render(template_name: str, **ctx) -> str:
    return _load_template(template_name).format(**ctx)


def _plaintext_fallback(code: str, ttl_minutes: int, app_name: str) -> str:
    return (
        f"Your {app_name} verification code is: {code}\n"
        f"It expires in {ttl_minutes} minutes.\n\n"
        "If you didn't request this, ignore this message."
    )


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class EmailService:
    """High-level façade callers use. Each method renders a template and hands
    the result off to the configured sender. All methods are async so callers
    can ``await`` them inside FastAPI routes."""

    def __init__(
        self,
        sender: _EmailSender | None = None,
        cfg: Settings | None = None,
    ) -> None:
        self._cfg = cfg or settings
        self._sender = sender or _build_sender(self._cfg)

    # ----- public API ------------------------------------------------------- #
    async def send_email_verification(
        self, *, to_address: str, full_name: str, code: str
    ) -> None:
        ctx = dict(
            app_name=self._cfg.email_from_name,
            full_name=full_name,
            code=code,
            ttl_minutes=self._cfg.otp_ttl_minutes,
        )
        await self._send(
            to_address=to_address,
            to_name=full_name,
            subject=f"Your {self._cfg.email_from_name} verification code",
            html=_render("email_verification.html", **ctx),
            text=_plaintext_fallback(code, self._cfg.otp_ttl_minutes, self._cfg.email_from_name),
        )

    async def send_password_reset(
        self, *, to_address: str, full_name: str, code: str
    ) -> None:
        ctx = dict(
            app_name=self._cfg.email_from_name,
            full_name=full_name,
            code=code,
            ttl_minutes=self._cfg.otp_ttl_minutes,
        )
        await self._send(
            to_address=to_address,
            to_name=full_name,
            subject=f"Reset your {self._cfg.email_from_name} password",
            html=_render("password_reset.html", **ctx),
            text=_plaintext_fallback(code, self._cfg.otp_ttl_minutes, self._cfg.email_from_name),
        )

    async def send_email_change(
        self, *, to_address: str, full_name: str, code: str
    ) -> None:
        ctx = dict(
            app_name=self._cfg.email_from_name,
            full_name=full_name,
            code=code,
            ttl_minutes=self._cfg.otp_ttl_minutes,
        )
        await self._send(
            to_address=to_address,
            to_name=full_name,
            subject=f"Confirm your new {self._cfg.email_from_name} email",
            html=_render("email_change.html", **ctx),
            text=_plaintext_fallback(code, self._cfg.otp_ttl_minutes, self._cfg.email_from_name),
        )

    # ----- internals -------------------------------------------------------- #
    async def _send(
        self,
        *,
        to_address: str,
        to_name: str | None,
        subject: str,
        html: str,
        text: str,
    ) -> None:
        msg = OutgoingEmail(
            to_address=to_address,
            to_name=to_name,
            subject=subject,
            html=html,
            text=text,
        )
        try:
            await self._sender.send(msg)
        except Exception:
            # Log but don't fail the calling request — email is best-effort.
            # Callers that need confirmed delivery should swap the sender for
            # one that raises and surface the error explicitly.
            LOG.exception("Failed to send email to %s", to_address)


__all__ = ["EmailService", "OutgoingEmail"]
