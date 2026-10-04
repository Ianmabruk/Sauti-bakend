"""Vendor authentication for the payment routes.

**Read this before trusting anything in here.**

Sauti has no account system. There is no password, no session, no JWT issuer, and
no login screen anywhere in the application — identity is an anonymous
per-browser id in ``localStorage`` that the backend has historically taken at
face value. Building a real identity system is a redesign of the whole
application and is explicitly out of scope for adding payments.

So what is implemented here is a *bearer token over the existing identity*, and
it is honestly weaker than real authentication. What it does buy:

* Subscription records cannot be read or written by a caller who has not been
  issued a token, and a token cannot be forged without ``SECRET_KEY`` (so a
  caller cannot invent a ``vendor_id`` pair and read someone else's billing
  history).
* Token issuance cannot take over a vendor that already has an owner.
* The money-critical facts stay server-side regardless: no amount, currency or
  activation decision is reachable by holding a token. Activating a
  subscription still requires a Paystack transaction that verifies, which is
  why a forged token is not a way to get free access.

What it does **not** buy: proof that the person holding a token is the person
who created the anonymous id. Sauti still has no password to check, and the
``user_id`` itself is client-generated.

That gap is narrowed here rather than left wide open. Token *issuance* requires a
per-user secret, stored on the ``users`` row as a SHA-256 digest and returned to
the browser exactly once (see :func:`authorize_token_issuance`). Before this, any
caller who learned a ``user_id`` could mint a token for it, because issuance
trusted a body field. Now the first request for an unused ``user_id`` claims its
secret and every later request has to present it, so learning an established
``user_id`` is no longer sufficient — the attacker also needs the secret that was
handed to the browser that owns it.

What remains is genuine: there is still no password, so anyone who obtains the
secret (by reading the victim's browser storage, or their machine) can act as that
user, and the secret cannot be rotated by proving identity, because proving
identity is the thing that is missing. Closing that properly means real accounts.
Until then, ``PAYSTACK_ALLOW_LIVE`` should stay ``false``. This is called out in
``docs/PAYSTACK_TEST_CHECKLIST.md``.

The signing key is ``SECRET_KEY``, the variable this application already uses
for the same purpose. Introducing a second signing secret would add a
configuration item that can drift out of sync with the first, for no gain.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from functools import wraps

from flask import current_app, g, jsonify, request

logger = logging.getLogger(__name__)

#: Version byte, so a future format change is detectable instead of decoding
#: into nonsense.
TOKEN_VERSION = "v1"

#: Header the browser sends the token in. ``Authorization: Bearer`` also works,
#: matching :func:`backend.security._resolve_admin_token` so the frontend has
#: one header convention for the whole backend.
VENDOR_TOKEN_HEADER = "X-Vendor-Token"


class VendorAuthError(Exception):
    """The caller is not a known vendor.

    ``status`` is the HTTP code the route should return: 401 when no credential
    was presented at all, 403 when one was presented and rejected.
    """

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class VendorPrincipal:
    """Who the caller is, as far as this token establishes."""

    user_id: str
    vendor_id: str
    expires_at: int

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


def _signing_key() -> bytes:
    """The HMAC key, derived from ``SECRET_KEY``.

    A domain-separation prefix is mixed in so a token signature can never
    collide with another HMAC this application computes over the same secret —
    for example the Flask session cookie. Without it, a token and a session
    signature are the same value and an attacker holding one could aim the other.
    """
    secret = current_app.config.get("SECRET_KEY") or ""
    return hashlib.sha256(f"sauti.vendor-token.{TOKEN_VERSION}".encode() + secret.encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _token_ttl() -> int:
    return int(current_app.config.get("PAYSTACK_TOKEN_TTL_SECONDS") or 604800)


def issue_vendor_token(user_id: str, vendor_id: str, *, ttl: int | None = None) -> dict:
    """Mint a token binding ``user_id`` to ``vendor_id``.

    Returns a dict rather than the raw string so the route can add an expiry it
    can show the caller without re-decoding what it just built.

    Args:
        user_id: The Sauti user this token acts as.
        vendor_id: The vendor being subscribed.
        ttl: Overrides the configured lifetime. Only the tests pass this.
    """
    lifetime = int(ttl if ttl is not None else _token_ttl())
    expires_at = int(time.time()) + lifetime

    body = {
        "v": TOKEN_VERSION,
        "uid": user_id,
        "vid": vendor_id,
        "exp": expires_at,
    }
    encoded = _b64(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(_signing_key(), encoded.encode(), hashlib.sha256).digest()

    return {
        "token": f"sauti_vendor.{encoded}.{_b64(signature)}",
        "expiresAt": expires_at,
        "expiresIn": lifetime,
    }


def decode_vendor_token(token: str | None) -> VendorPrincipal:
    """Validate a token and return who it is for.

    Every rejection path raises the same :class:`VendorAuthError` with a generic
    message. Distinguishing "expired" from "bad signature" from "malformed" for
    the caller would tell an attacker which of the three they got wrong, and
    there is nothing actionable they could do with the difference.

    Raises:
        VendorAuthError: The token is missing, malformed, unsigned, forged or
            expired.
    """
    invalid = VendorAuthError("Authentication required", 401)

    if not token or not isinstance(token, str):
        raise invalid

    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != "sauti_vendor":
        raise invalid

    _, encoded, supplied_signature = parts

    expected = hmac.new(_signing_key(), encoded.encode(), hashlib.sha256).digest()
    try:
        provided = _unb64(supplied_signature)
    except (ValueError, base64.binascii.Error) as exc:
        raise invalid from exc

    # Constant-time, and checked before the body is parsed. The signature covers
    # the body, so trusting the body first would mean acting on unauthenticated
    # input.
    if not hmac.compare_digest(expected, provided):
        logger.warning("Rejected a vendor token with an invalid signature")
        raise invalid

    try:
        body = json.loads(_unb64(encoded))
    except (ValueError, base64.binascii.Error) as exc:
        raise invalid from exc

    if not isinstance(body, dict) or body.get("v") != TOKEN_VERSION:
        raise invalid

    user_id = body.get("uid")
    vendor_id = body.get("vid")
    expires_at = body.get("exp")

    if not isinstance(user_id, str) or not user_id:
        raise invalid
    if not isinstance(vendor_id, str) or not vendor_id:
        raise invalid
    if not isinstance(expires_at, int):
        raise invalid

    principal = VendorPrincipal(
        user_id=user_id, vendor_id=vendor_id, expires_at=expires_at
    )
    if principal.is_expired:
        raise VendorAuthError("Session expired. Please sign in again.", 401)

    return principal


def _presented_token() -> str | None:
    """The vendor token on this request, from either supported header."""
    token = request.headers.get(VENDOR_TOKEN_HEADER, "").strip()
    if not token:
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            candidate = authorization[7:].strip()
            # A Paystack secret key would never appear here, but the check keeps
            # a misconfigured proxy from forwarding one into a log line.
            if not candidate.startswith("sk_"):
                token = candidate
    return token or None


# ---------------------------------------------------------------------------
# Issuance secrets
# ---------------------------------------------------------------------------

#: Bytes of entropy in an issuance secret. 32 bytes is the standard choice and
#: costs a browser nothing to store.
SECRET_BYTES = 32


class VendorSecretError(Exception):
    """The caller's issuance secret was missing or wrong.

    ``status`` is the HTTP code the route should return.
    """

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.message = message
        self.status = status


def hash_vendor_secret(secret: str) -> str:
    """The stored form of an issuance secret.

    SHA-256 rather than a password hash on purpose. This is a 256-bit random
    value with no guessable structure and no need to resist offline attack at
    low cost, so bcrypt's work factor would only add latency to every checkout
    page load. A plain digest is enough because there is nothing to brute-force:
    the input space is 2^256.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_vendor_secret() -> str:
    """A fresh issuance secret, as a URL-safe string."""
    return secrets.token_urlsafe(SECRET_BYTES)


def authorize_token_issuance(user_id: str, presented_secret: str | None) -> tuple[str, bool]:
    """Decide whether this caller may mint a vendor token for ``user_id``.

    Returns ``(secret, is_new)``. ``is_new`` is True the first time a secret is
    created for a user, and the caller must then hand the secret back to the
    browser, which is the only time it is ever transmitted.

    A user with no stored digest is issued one. A user who already has a secret
    must present it.

    Raises:
        VendorSecretError: the caller presented no secret, or the wrong one, for
            a user that already has one.
    """
    from ..db import db
    from ..models import User

    user = db.session.get(User, user_id)

    if user is None:
        # Not a user yet. The route creates the row and stores this secret on it.
        secret = new_vendor_secret()
        return secret, True

    if user.vendor_token_hash is None:
        secret = new_vendor_secret()
        user.vendor_token_hash = hash_vendor_secret(secret)
        db.session.commit()
        logger.info("Issued a first vendor-token secret for user %s", user_id)
        return secret, True

    if not presented_secret or not isinstance(presented_secret, str):
        logger.warning(
            "Refused a vendor token for user %s: no issuance secret presented",
            user_id,
        )
        raise VendorSecretError("This account has not been verified. Sign in again.", 403)

    if not hmac.compare_digest(user.vendor_token_hash, hash_vendor_secret(presented_secret)):
        logger.warning(
            "Refused a vendor token for user %s: issuance secret did not match",
            user_id,
        )
        raise VendorSecretError("This account has not been verified. Sign in again.", 403)

    return presented_secret, False


def require_vendor(view):
    """Protect a route so it runs as a known vendor.

    On success the principal is stashed on ``g.vendor_principal``; the wrapped
    view reads it from there rather than threading it through its signature.

    Usage::

        @payments_bp.route("/payments/status")
        @require_vendor
        def status():
            principal = g.vendor_principal
            ...

    Error responses are the flat ``{"error": "..."}`` shape the rest of this
    backend uses, so the frontend's single error-narrowing path handles them.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            g.vendor_principal = decode_vendor_token(_presented_token())
        except VendorAuthError as exc:
            return jsonify({"error": exc.message}), exc.status
        return view(*args, **kwargs)

    return wrapped


__all__ = [
    "VENDOR_TOKEN_HEADER",
    "VendorAuthError",
    "VendorPrincipal",
    "VendorSecretError",
    "authorize_token_issuance",
    "decode_vendor_token",
    "hash_vendor_secret",
    "issue_vendor_token",
    "new_vendor_secret",
    "require_vendor",
]
