# Email + Auth Setup Guide

This guide covers everything you need to wire up the email-OTP authentication
flow that ships with DukaMate: dependencies, environment variables, Gmail SMTP
configuration, local testing, and how to swap to a transactional email
provider (SendGrid, SES, Mailgun, Resend) when you outgrow Gmail.

## 1. Python packages

The auth flow itself needs no new packages beyond what's already in
``requirements.txt``. For real SMTP delivery, add `aiosmtplib`:

```bash
pip install aiosmtplib
```

That's it. No Jinja2 or template engine — the HTML templates use Python
``str.format`` and live in ``app/core/email_templates/``.

If you'd rather render with Jinja2 later, swap ``_render()`` in
``app/core/email_service.py`` to use a ``jinja2.Environment``; the rest of the
service is unchanged.

## 2. Environment variables

Everything is read from the environment via ``app/core/config.py``. The
auth-relevant variables are:

| Variable | Default | Purpose |
|----------|---------|---------|
| `JWT_SECRET_KEY` | dev-only | Symmetric secret for signing JWTs. **Required in prod.** Generate with `openssl rand -hex 32`. |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access token lifetime. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh token lifetime. |
| `BCRYPT_ROUNDS` | `12` | Password hashing cost. 12 is appropriate for production; lower to `4` for tests/dev. |
| `OTP_LENGTH` | `6` | Digits in each OTP. |
| `OTP_TTL_MINUTES` | `10` | How long an OTP stays valid. |
| `OTP_RESEND_COOLDOWN_SECONDS` | `60` | Minimum seconds between two OTP issuances per (user, verification_type). |
| `EMAIL_PROVIDER` | `smtp` | Pluggable provider name (`smtp` ships; others extend `_build_sender`). |
| `EMAIL_FROM_ADDRESS` | `no-reply@dukamate.app` | The `From` header on outgoing emails. |
| `EMAIL_FROM_NAME` | `DukaMate` | Display name on outgoing emails. |
| `EMAIL_LOG_ONLY` | `false` | When `true`, OTP emails are logged instead of sent. Use in CI/dev. |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server. |
| `SMTP_PORT` | `587` | SMTP port. 587 for STARTTLS, 465 for implicit TLS. |
| `SMTP_USERNAME` | (empty) | Your SMTP login (often the From address). |
| `SMTP_PASSWORD` | (empty) | Your SMTP password / app password / API key. |
| `SMTP_USE_TLS` | `true` | STARTTLS on port 587. |
| `SMTP_USE_SSL` | `false` | Implicit TLS. Use with port 465; set `SMTP_USE_TLS=false`. |
| `SMTP_TIMEOUT_SECONDS` | `30` | Socket timeout. |

Place these in a ``.env`` file at the project root. Pydantic Settings reads it
on startup. Do **not** commit the file — `.env` should be in `.gitignore`.

Example `.env` for local Gmail:

```bash
JWT_SECRET_KEY=$(openssl rand -hex 32)
BCRYPT_ROUNDS=12

EMAIL_FROM_ADDRESS=you@gmail.com
EMAIL_FROM_NAME=DukaMate Dev

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx          # Gmail App Password, NOT your account password
SMTP_USE_TLS=true
```

## 3. Configuring Gmail SMTP

Gmail won't let third-party apps use your real password. You need an **App
Password**, which only works on accounts with 2-Step Verification enabled.

### Step-by-step

1. Open **https://myaccount.google.com/security**.
2. Under **"How you sign in to Google"**, turn on **2-Step Verification** if
   it isn't already. (Gmail won't show the App Passwords option without it.)
3. Once 2-Step is on, open **https://myaccount.google.com/apppasswords**
   directly. Sign in again if prompted.
4. In the **"App name"** field, type something memorable like `DukaMate dev`,
   and click **Create**.
5. Google shows a 16-character password like `abcd efgh ijkl mnop`. Copy it
   (spaces optional — strip them or keep them; SMTP accepts both).
6. Paste the value into `SMTP_PASSWORD`. Put your Gmail address in
   `SMTP_USERNAME` and `EMAIL_FROM_ADDRESS`.

That's it. Don't enable "Less secure app access" — Google removed it years
ago, and App Passwords are the supported path.

### Gmail rate / sending limits

- Free Gmail: ~500 messages/day, ~100/hour.
- Google Workspace: ~2,000/day.

Gmail is fine for development, internal tools, and very early stage. The
moment you have real users registering, switch to a transactional provider
(below).

## 4. Local testing

Two options depending on what you want to test.

### A. No SMTP at all — log the emails

Best for fast unit tests, CI, and when you don't want to spam your own inbox:

```bash
EMAIL_LOG_ONLY=true uvicorn app.main:app --reload
```

The EmailService now writes to the log every time an OTP "would" be sent:

```
INFO dukamate.email [email-log-only] to=alice@example.com subject='Your DukaMate verification code' preview='Your DukaMate verification code is: 482917...'
```

You can also read the OTP straight from the database while developing:

```sql
SELECT code, expires_at, used_at
FROM verification_codes
WHERE user_id = (SELECT id FROM users WHERE email='alice@example.com')
  AND verification_type='EMAIL_VERIFICATION'
ORDER BY created_at DESC LIMIT 1;
```

The test suite uses this same pattern via the ``_latest_otp_for`` helper in
``tests/conftest.py``.

### B. Real SMTP — your own inbox

Set the Gmail variables from §2, leave `EMAIL_LOG_ONLY=false`, and register
against your own address:

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"full_name":"You","email":"you@gmail.com"}'
```

The OTP arrives in your Gmail within seconds. Use it to call `/auth/verify-email`,
then `/auth/set-password`. Watch your terminal for `INFO dukamate.email` log
lines confirming each send.

### C. Local SMTP catcher

If you don't want to spam an inbox but still want to verify rendered HTML,
run **MailHog** or **Mailpit** locally:

```bash
docker run -d -p 1025:1025 -p 8025:8025 axllent/mailpit
```

Then in `.env`:

```bash
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_USE_TLS=false
```

Open `http://localhost:8025` to see every email the app sends, including the
rendered HTML and the plaintext fallback.

## 5. Switching providers

The EmailService is **provider-agnostic by design**: methods like
``send_email_verification`` build an ``OutgoingEmail`` dataclass and hand it
to a ``_EmailSender`` strategy. Adding a new provider is a one-class change
in ``app/core/email_service.py``, with **no edits to AuthService or any
caller**.

Open ``_build_sender`` in ``app/core/email_service.py`` and add the branch:

```python
def _build_sender(s: Settings) -> _EmailSender:
    if s.email_log_only:
        return _LogOnlySender()
    provider = (s.email_provider or "smtp").lower()
    if provider == "smtp":
        return _SMTPSender(s)
    if provider == "sendgrid":
        return _SendGridSender(s)        # add the class below
    if provider == "ses":
        return _SESSender(s)
    if provider == "mailgun":
        return _MailgunSender(s)
    if provider == "resend":
        return _ResendSender(s)
    raise ValueError(f"Unknown email_provider '{provider}'")
```

Each sender just implements ``async def send(self, msg: OutgoingEmail) -> None``.
Sketches below.

### SendGrid

```python
class _SendGridSender(_EmailSender):
    def __init__(self, s: Settings) -> None:
        self._api_key = s.sendgrid_api_key   # add to config.Settings
        self._from = s.email_from_address
        self._from_name = s.email_from_name

    async def send(self, msg: OutgoingEmail) -> None:
        import httpx
        payload = {
            "personalizations": [{"to": [{"email": msg.to_address, "name": msg.to_name}]}],
            "from": {"email": self._from, "name": self._from_name},
            "subject": msg.subject,
            "content": [
                {"type": "text/plain", "value": msg.text},
                {"type": "text/html",  "value": msg.html},
            ],
        }
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            r.raise_for_status()
```

Set `EMAIL_PROVIDER=sendgrid` and `SENDGRID_API_KEY=SG.…`. No other change.

### Amazon SES

Use `aioboto3` and the SES `send_raw_email` API. The `MIMEMultipart` message
construction is identical to the SMTP path; just hand it to
`ses.send_raw_email(RawMessage=...)`.

### Mailgun

POST to `https://api.mailgun.net/v3/<domain>/messages` with HTTP Basic auth
(`api:<your-api-key>`) and form fields `from`, `to`, `subject`, `text`, `html`.

### Resend

POST to `https://api.resend.com/emails` with `Authorization: Bearer <api-key>`
and the JSON body `{from, to, subject, html, text}`.

### Why the business logic doesn't change

The auth service only calls three methods on `EmailService`:
``send_email_verification``, ``send_password_reset``, ``send_email_change``.
Those methods render the template, build the `OutgoingEmail`, and call
`self._sender.send(msg)`. Swapping the sender is purely a transport choice —
the service interface, the templates, the JWT issuance, the OTP table, the
rate-limit logic: all untouched.

## 6. Operational notes

- **Email failures don't fail registration.** The service logs and continues.
  This is deliberate — the OTP exists in `verification_codes` even if delivery
  fails, so a manual or scripted resend (``POST /auth/resend-otp``) can recover.
  If you want stricter semantics (refuse registration unless email delivery
  succeeded), make ``_EmailSender.send`` re-raise on failure.
- **Cleanup.** Used or expired OTP rows accumulate. Run
  ``python -m app.workers.otp_cleanup`` daily (cron `30 2 * * *`) to keep the
  table small. Default retention is 7 days for used/expired rows.
- **OTP brute-force protection.** A 6-digit OTP has 10⁶ possibilities and a
  10-minute window. The resend cooldown (60 s) caps the rate of new codes. For
  high-value flows (banking, etc.), add an attempt counter on
  `verification_codes` and lock the user after N failed verify-email calls.
- **Email enumeration.** `forgot-password` and `resend-otp` for
  `PASSWORD_RESET` deliberately return the same success message for unknown
  emails. Don't change that — it's what stops attackers using these endpoints
  to test which addresses are registered.
- **2FA.** When you're ready to add SMS-based 2FA, add a fourth
  `verification_type` (e.g. `LOGIN_2FA`) — the table and the OTP utilities are
  ready for it.

## 7. Endpoint reference

| Endpoint | Purpose | Returns |
|----------|---------|---------|
| `POST /auth/register` | Create unverified user + send EMAIL_VERIFICATION OTP | user (no tokens) |
| `POST /auth/verify-email` | Consume OTP, mark email verified | user |
| `POST /auth/resend-otp` | Re-issue EMAIL_VERIFICATION or PASSWORD_RESET OTP (rate-limited) | message |
| `POST /auth/set-password` | First-time password creation (after verify) | user + tokens |
| `POST /auth/login` | Email + password | user + tokens |
| `POST /auth/forgot-password` | Send PASSWORD_RESET OTP (silent if unknown) | message |
| `POST /auth/reset-password` | Consume OTP + set new password | message |
| `POST /auth/refresh` | New access token from a refresh token | access token |
| `POST /auth/logout` | Stateless (client discards tokens) | message |
| `GET /auth/me` | Current user | user |

Done. The auth subsystem is ready to ship.
