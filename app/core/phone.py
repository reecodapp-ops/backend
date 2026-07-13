"""Shared phone number validation & normalization.

Uses Google's libphonenumber via the ``phonenumbers`` PyPI package -- the
same source of truth the country-phone-codes migration's calling-code seed
data was generated from, so a number that validates here is trustworthy
against the exact same reference data the country picker's calling codes
came from.

USAGE
-----
Every write path that accepts a phone number (business, customer, supplier)
accepts an optional structured pair -- ``phone_country_id`` +
``phone_local_number`` -- alongside the existing free-text ``phone_number``
field, which stays exactly as it worked before this feature (additive, not a
breaking change). When the structured pair is given, the service layer:

    1. Looks up the ``Country`` row for ``phone_country_id`` (for its
       ``calling_code`` and ``iso_code``).
    2. Calls ``normalize_phone_number(...)`` here.
    3. Stores the resulting E.164 string in the existing ``phone_number``
       column -- no schema change needed on businesses/customers/suppliers.

If only the legacy free-text ``phone_number`` is given, nothing here is
invoked at all -- that field's own schema-level regex validation
(``^\\+\\d{8,15}$``) is unchanged.
"""
from __future__ import annotations

import phonenumbers
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError


def normalize_phone_number(
    *, calling_code: str, iso_code: str, local_number: str
) -> str:
    """Combine a country's calling code with a locally-entered number and
    return the validated, normalized E.164 string (e.g. ``+256700111222``).

    Raises ``ValidationError(code="invalid_phone_number")`` -- never a bare
    ``ValueError`` -- so every call site gets the same 422 field-level error
    shape without having to know about ``phonenumbers`` itself.
    """
    raw = (local_number or "").strip()
    if not raw:
        raise ValidationError(
            "Phone number is required when a country is selected.",
            code="invalid_phone_number",
        )

    parsed = None
    # Primary path: let phonenumbers interpret the number the way a person
    # would actually type it for their own country (handles national
    # dialing prefixes like a leading 0 correctly, which naively
    # concatenating calling_code + local_number would not).
    try:
        parsed = phonenumbers.parse(raw, iso_code)
    except phonenumbers.NumberParseException:
        parsed = None

    if parsed is None or not phonenumbers.is_valid_number(parsed):
        # Fallback: the local number may already have been entered in a
        # form phonenumbers couldn't place regionally on its own (e.g. with
        # its own leading +). Try combining calling_code + digits directly.
        digits = "".join(ch for ch in raw if ch.isdigit())
        digits = digits.lstrip("0")
        try:
            candidate = phonenumbers.parse(f"{calling_code}{digits}", None)
        except phonenumbers.NumberParseException:
            candidate = None
        if candidate is not None and phonenumbers.is_valid_number(candidate):
            parsed = candidate

    if parsed is None or not phonenumbers.is_valid_number(parsed):
        raise ValidationError(
            "That doesn't look like a valid phone number for the selected country.",
            code="invalid_phone_number",
        )

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


async def resolve_phone_number(
    session: AsyncSession,
    *,
    phone_country_id: int | None,
    phone_local_number: str | None,
    legacy_phone_number: str | None,
) -> str | None:
    """The one call every write path (Business/Customer/Supplier
    create/update) makes to turn its request's phone fields into the value
    that actually gets stored in the model's ``phone_number`` column.

    - Structured pair given (``phone_country_id`` set -- the schema's own
      ``PhonePairFields`` validator already guarantees ``phone_local_number``
      is set too whenever this is) -> look up the ``Country``, normalize,
      and that wins, regardless of whatever ``legacy_phone_number`` also
      contains.
    - Structured pair absent -> ``legacy_phone_number`` passes through
      completely unchanged (it's already been validated by the schema's own
      regex field validator) -- this is what keeps every existing caller
      that only ever sent a plain ``phone_number`` string working exactly as
      before.
    - Neither given -> ``None`` (phone number stays optional everywhere it
      already is).
    """
    if phone_country_id is None:
        return legacy_phone_number

    # Local import to avoid a module-level circular import between
    # app.core.phone and app.models.lookups.
    from app.models.lookups import Country

    country = await session.get(Country, phone_country_id)
    if country is None:
        raise ValidationError(
            "Unknown phone_country_id.", code="invalid_phone_country"
        )
    return normalize_phone_number(
        calling_code=country.calling_code,
        iso_code=country.iso_code,
        local_number=phone_local_number or "",
    )


def parse_phone_number(e164_number: str) -> dict:
    """Split an E.164 string back into its country calling code and
    national-format local number -- backs the optional
    ``GET /phone-numbers/parse`` helper endpoint, for a frontend that would
    rather not duplicate ``phonenumbers`` parsing logic itself.

    Raises ``ValidationError(code="invalid_phone_number")`` if the string
    isn't a parseable, valid E.164 number.
    """
    try:
        parsed = phonenumbers.parse(e164_number, None)
    except phonenumbers.NumberParseException:
        raise ValidationError(
            "Not a valid E.164 phone number.", code="invalid_phone_number"
        )
    if not phonenumbers.is_valid_number(parsed):
        raise ValidationError(
            "Not a valid E.164 phone number.", code="invalid_phone_number"
        )
    region = phonenumbers.region_code_for_number(parsed)
    national = phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.NATIONAL
    )
    return {
        "calling_code": f"+{parsed.country_code}",
        "iso_code": region,
        "local_number": national,
        "e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
    }
