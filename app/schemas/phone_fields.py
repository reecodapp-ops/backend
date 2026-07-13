"""Shared structured phone-number input fields.

``phone_country_id`` + ``phone_local_number`` is how a country-code picker +
local-number input naturally splits on the frontend -- two fields, not a
pre-formatted string to re-parse. Every schema that accepts a phone number
(Business, Customer, Supplier create/update requests) mixes this in
alongside its own existing free-text ``phone_number`` field, which is left
completely untouched for backward compatibility: this is purely additive.

The actual E.164 combination + validation (which needs the target
``Country`` row's ``calling_code``/``iso_code``, i.e. a DB read) happens at
the service layer via ``app.core.phone.normalize_phone_number`` -- this
mixin only enforces the cheap, DB-free "both or neither" shape rule.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class PhonePairFields(BaseModel):
    phone_country_id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "References `countries.id`. Provide together with "
            "phone_local_number for a validated, normalized E.164 phone "
            "number -- an alternative to sending a pre-formatted "
            "phone_number string directly."
        ),
    )
    phone_local_number: str | None = Field(
        default=None,
        max_length=20,
        description="The number as the person would type it for their own country, e.g. '0700111222'.",
    )

    @model_validator(mode="after")
    def _phone_pair_together(self) -> "PhonePairFields":
        if (self.phone_country_id is None) != (self.phone_local_number is None):
            raise ValueError(
                "phone_country_id and phone_local_number must be provided together."
            )
        return self


__all__ = ["PhonePairFields"]
