"""Pydantic schemas for the payment and subscription endpoints.

These form the request-validation boundary. Everything a browser sends about a
payment is a hint; nothing here is trusted as a fact. Note what is *absent*: no
amount, no currency, no plan code, no vendor id that decides who is charged.

The one field worth arguing about is ``email``. Paystack requires a real address
on ``transaction/initialize``, and the browser has no way to know a vendor's
address the server can verify — there is no account to read it from. It is
therefore accepted from the client, but strictly bounded: max length, a shape
check, and a rate limit. It influences where a receipt goes, not whether money
moves or what it costs.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

#: Intentionally permissive. RFC 5322 is not what a Kenyan vendor types into a
#: checkout field, and Paystack is the party that decides whether it can deliver
#: the receipt. This only rejects input that could not be an address at all.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class VendorTokenRequest(BaseModel):
    """Ask for a vendor token.

    ``user_id`` is the same anonymous per-browser id the rest of Sauti already
    uses for saved items and memory. It does not grant access to an existing
    vendor owned by someone else; see :func:`backend.payments.service.
    resolve_vendor_identity`.

    ``secret`` authorises issuance for a user that has already been issued one.
    It is required for every request except the very first a given ``user_id``
    makes, and it is returned exactly once, by that first request. See
    :func:`backend.payments.auth.authorize_token_issuance`.
    """

    user_id: str = Field(min_length=3, max_length=120)
    vendor_id: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    secret: str | None = Field(default=None, max_length=128)

    @field_validator("user_id", "vendor_id", "secret")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip().lower()
        if not candidate:
            return None
        if not _EMAIL_PATTERN.match(candidate):
            raise ValueError("Enter a valid email address")
        return candidate


class PaymentInitializeRequest(BaseModel):
    """Start a checkout.

    Intentionally has no amount field. Accepting one — even an optional one that
    is then checked — invites the pattern where the check is the thing that gets
    deleted during an incident. The amount is read from the plan row on the
    server and there is no code path by which a request body can influence it.
    """

    #: Optional so the endpoint can serve every vendor from one plan for now.
    #: When present it selects a row from ``plans``; it never carries a price.
    plan: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=255)

    @field_validator("plan")
    @classmethod
    def _strip_plan(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip().lower()
        return candidate or None

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str | None) -> str | None:
        return VendorTokenRequest._check_email(value)


__all__ = ["VendorTokenRequest", "PaymentInitializeRequest"]