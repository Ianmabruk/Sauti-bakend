"""Paystack API client and webhook signature verification.

Three rules govern this module.

1. **The secret key never leaves the server.** It appears in exactly two places
   here — the ``Authorization`` header on outbound requests, and the HMAC used
   to check inbound webhooks. It is never logged, never returned, and never
   passed to an error message. Nothing in this file may be imported by frontend
   code, and no function here accepts a secret as an argument, so a caller
   cannot thread one through to a response body by accident.

2. **Test mode is the default and live mode is opt-in.** A live secret key is
   refused unless ``PAYSTACK_ALLOW_LIVE`` is set, because the failure mode of the
   alternative — a live key pasted into a test deployment — is charging real
   money to real people, which is not recoverable by deploying a fix.

3. **Amounts and currency come from the database, never from the caller.** This
   module takes an already-resolved amount and sends it. The decision about what
   a vendor owes lives in :mod:`backend.payments.service`.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from flask import current_app

logger = logging.getLogger(__name__)

#: Prefixes Paystack uses to mark a key's environment. The key itself is never
#: logged; only which of these it starts with, and only in test/live assertions.
TEST_KEY_PREFIX = "sk_test_"
LIVE_KEY_PREFIX = "sk_live_"
TEST_PUBLIC_PREFIX = "pk_test_"
LIVE_PUBLIC_PREFIX = "pk_live_"


class PaystackError(Exception):
    """Paystack could not be reached, or refused the request.

    Carries a message safe to show a user. The upstream body may contain
    provider detail that is useful in a log but confusing in a browser, so the
    two are kept apart: :attr:`detail` goes to the log, :attr:`user_message` to
    the client.
    """

    def __init__(self, user_message: str, *, detail: str = "", status: int = 0):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail
        self.status = status


class PaystackDisabledError(PaystackError):
    """Paystack is not usable in this deployment's current configuration."""


# ---------------------------------------------------------------------------
# Configuration access
# ---------------------------------------------------------------------------


def _config(key: str, default: Any = "") -> Any:
    """Read a Paystack setting from the active Flask config."""
    if current_app:
        return current_app.config.get(key, default)
    return default


def paystack_mode() -> str:
    """Return ``"test"``, ``"live"`` or ``"disabled"``.

    Derived from the secret key's prefix rather than a separate setting, because
    the prefix is the ground truth: a key and a mode flag can disagree, and the
    key is what actually determines where the money goes.
    """
    secret = (_config("PAYSTACK_SECRET_KEY") or "").strip()

    if not secret:
        return "disabled"
    if secret.startswith(LIVE_KEY_PREFIX):
        return "live"
    if secret.startswith(TEST_KEY_PREFIX):
        return "test"
    # An unrecognised prefix is treated as disabled rather than guessed at.
    return "disabled"


def is_test_mode() -> bool:
    return paystack_mode() == "test"


def assert_paystack_mode() -> str:
    """Validate the configured credentials, returning the mode.

    Raises:
        PaystackDisabledError: no usable key is configured, or a live key is
            configured while ``PAYSTACK_ALLOW_LIVE`` is not set.
    """
    mode = paystack_mode()
    secret = (_config("PAYSTACK_SECRET_KEY") or "").strip()

    if mode == "disabled":
        if not secret:
            raise PaystackDisabledError(
                "Payments are not configured yet. Please try again shortly."
            )
        # Deliberately does not echo the key or its length.
        logger.error(
            "PAYSTACK_SECRET_KEY does not start with %s or %s. Payments are "
            "disabled until it is corrected.",
            TEST_KEY_PREFIX,
            LIVE_KEY_PREFIX,
        )
        raise PaystackDisabledError(
            "Payments are not configured yet. Please try again shortly."
        )

    if mode == "live" and not _config("PAYSTACK_ALLOW_LIVE"):
        logger.error(
            "A Paystack live secret key is configured but PAYSTACK_ALLOW_LIVE "
            "is not set. Refusing to process payments so a test deployment "
            "cannot charge real money. Set PAYSTACK_ALLOW_LIVE=true only on a "
            "deployment that is genuinely taking live payments."
        )
        raise PaystackDisabledError(
            "Payments are not available right now. Please try again shortly."
        )

    return mode


def paystack_public_key() -> str:
    """The public key, safe to hand to the browser.

    Only ever the ``pk_`` value. There is no code path through this function
    that can return an ``sk_`` key, which is what makes it safe for the
    subscription-status endpoint to include it.
    """
    public_key = (_config("PAYSTACK_PUBLIC_KEY") or "").strip()
    if public_key.startswith(TEST_PUBLIC_PREFIX) or public_key.startswith(
        LIVE_PUBLIC_PREFIX
    ):
        return public_key

    if public_key:
        logger.error(
            "PAYSTACK_PUBLIC_KEY does not start with %s or %s. It will not be "
            "sent to the browser.",
            TEST_PUBLIC_PREFIX,
            LIVE_PUBLIC_PREFIX,
        )
    return ""


def web_url() -> str:
    """The base URL of Paystack's hosted checkout.

    Paystack resolves the live/test split from the secret key, so this is a
    single endpoint for both. It is a function rather than a constant so a
    Paystack sandbox can be pointed at for tests without editing code.
    """
    return str(_config("PAYSTACK_API_URL") or "https://api.paystack.co").rstrip("/")


# ---------------------------------------------------------------------------
# Webhook signature
# ---------------------------------------------------------------------------


def compute_signature(raw_body: bytes, secret: str | None = None) -> str:
    """HMAC-SHA512 of the raw request body, hex encoded.

    This is Paystack's documented scheme: SHA-512, not SHA-256, and over the
    *raw* bytes — not a re-serialisation of the parsed JSON. Re-encoding changes
    key order and whitespace, and a signature computed over a re-encoding never
    matches one computed over the original body.

    Args:
        raw_body: The exact bytes Flask read off the socket.
        secret: Overrides the configured key. Present so the tests can compute
            an expected signature without touching app config; no route passes it.
    """
    key = secret if secret is not None else (_config("PAYSTACK_SECRET_KEY") or "")
    return hmac.new(
        key.encode("utf-8"), raw_body or b"", hashlib.sha512
    ).hexdigest()


#: A Paystack signature is a hex-encoded SHA-512 digest: exactly 128 characters.
#: Validating the shape before comparing is not just tidiness — see below.
_SIGNATURE_PATTERN = re.compile(r"^[0-9a-f]{128}$")


def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    """True when ``signature`` is the correct HMAC-SHA512 for ``raw_body``.

    A missing or empty signature is False, not an error to be handled upstream.
    An unsigned webhook is an unsigned webhook.

    **Fails closed when no usable key is configured.** This is the single most
    important behaviour in the module. :func:`compute_signature` substitutes an
    empty string for a missing key, and an HMAC keyed with the empty string is
    reproducible by anyone — it is not a secret, it is the absence of one. Without
    the guard below, a deployment that has not yet been given its
    ``PAYSTACK_SECRET_KEY`` would accept *any* webhook body as authentic, and a
    forged ``charge.success`` would activate a subscription for free. A
    deployment where payments are not configured must reject webhooks, not accept
    them.

    The comparison is constant-time. This is not theatre: the signature is the
    only thing standing between Paystack and anyone who can reach the endpoint,
    and a timing-variable comparison is the standard way to recover a MAC one
    byte at a time.
    """
    if not signature or not isinstance(signature, str):
        return False

    if paystack_mode() not in ("test", "live"):
        # No key, or a key whose prefix is not recognised. Either way there is
        # nothing to verify against, so nothing can be trusted.
        logger.error(
            "Rejecting a webhook: PAYSTACK_SECRET_KEY is unset or unrecognised, "
            "so no signature can be authenticated."
        )
        return False

    candidate = signature.strip().lower()

    # Shape check before compare_digest. Two reasons, both real:
    #   1. compare_digest raises TypeError when either str operand contains
    #      non-ASCII characters, so an arbitrary attacker-controlled header
    #      would otherwise produce an unhandled 500 on a public endpoint.
    #   2. It bounds the comparison to a known-length value.
    if not _SIGNATURE_PATTERN.match(candidate):
        return False

    expected = compute_signature(raw_body)
    return hmac.compare_digest(expected, candidate)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InitializedTransaction:
    """What the browser needs to open Paystack's checkout.

    Deliberately contains nothing else. Paystack's initialize response also
    carries fields such as ``session_id`` and the raw plan metadata; none of
    them are needed to open the payment UI, and the narrower this type is, the
    less chance of one being reflected into a response later.
    """

    authorization_url: str
    access_code: str
    reference: str


@dataclass(frozen=True)
class VerifiedTransaction:
    """The subset of a verified Paystack transaction Sauti acts on.

    Every field is taken from Paystack's own verify response. None of it comes
    from the browser, which is the entire point: a callback that forwards "it
    worked" must not be able to move this.
    """

    reference: str
    status: str
    amount_minor: int
    currency: str
    paid: bool
    channel: str | None
    customer_code: str | None
    customer_email: str | None
    paid_at: str | None
    failure_reason: str | None


class PaystackClient:
    """Thin synchronous wrapper over the three Paystack endpoints Sauti needs."""

    def __init__(self) -> None:
        self.mode = assert_paystack_mode()
        self.timeout = float(_config("PAYSTACK_TIMEOUT", 15))

    def _headers(self) -> dict[str, str]:
        # Rebuilt per request from config rather than cached on the instance, so
        # a key rotation takes effect on the next call instead of needing a
        # restart.
        return {
            "Authorization": f"Bearer {_config('PAYSTACK_SECRET_KEY')}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post(self, path: str, payload: dict) -> dict:
        return self._send("POST", path, json=payload)

    def _get(self, path: str, params: dict) -> dict:
        return self._send("GET", path, params=params)

    def _send(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        url = f"{web_url()}{path}"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    method, url, json=json, params=params, headers=self._headers()
                )
        except httpx.TimeoutException as exc:
            # A timeout is ambiguous: Paystack may or may not have created the
            # transaction. The message says so rather than claiming failure.
            logger.warning("Paystack request to %s timed out: %s", path, exc)
            raise PaystackError(
                "The payment service did not respond in time. Please try again.",
                detail="timeout",
                status=504,
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("Paystack request to %s failed: %s", path, exc)
            raise PaystackError(
                "The payment service is temporarily unavailable. Please try again.",
                detail=str(exc),
                status=502,
            ) from exc

        try:
            body = response.json()
        except ValueError as exc:
            logger.error(
                "Paystack returned a non-JSON body for %s (status %s)", path, response.status_code
            )
            raise PaystackError(
                "The payment service returned an unexpected response.",
                detail="non-json",
                status=502,
            ) from exc

        if response.status_code != 200 or not body.get("status"):
            message = str(body.get("message") or "unknown error")
            logger.warning(
                "Paystack refused %s (status %s): %s", path, response.status_code, message
            )
            raise PaystackError(
                "The payment could not be started. Please try again.",
                detail=message,
                status=response.status_code,
            )

        return body

    def initialize_transaction(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        email: str,
        plan_code: str | None,
        callback_url: str | None = None,
    ) -> InitializedTransaction:
        """Create a transaction and return where to send the customer.

        ``amount_minor`` and ``currency`` are passed through untouched. This is
        the last point where they exist before Paystack sees them, and the
        server is the only party that gets to decide them: both are read from the
        resolved :class:`~backend.payments.models.Plan` row, never from the
        request body.
        """
        payload: dict[str, Any] = {
            "reference": reference,
            "amount": int(amount_minor),
            "email": email,
            "currency": currency,
        }
        if plan_code:
            # A plan code is what makes the charge recur. Sending one turns a
            # one-off transaction into a standing subscription on Paystack's
            # side, which is the behaviour a monthly vendor fee wants.
            payload["plan"] = plan_code
        if callback_url:
            payload["callback_url"] = callback_url

        body = self._post("/transaction/initialize", payload)
        data = body.get("data") or {}

        authorization_url = data.get("authorization_url")
        access_code = data.get("access_code")
        if not authorization_url:
            # Paystack reported success but gave nowhere to send the customer.
            # Treating this as an error is better than opening an empty popup.
            logger.error(
                "Paystack initialize for a transaction returned no authorization_url"
            )
            raise PaystackError(
                "The payment could not be started. Please try again.",
                detail="missing authorization_url",
                status=502,
            )

        return InitializedTransaction(
            authorization_url=str(authorization_url),
            access_code=str(access_code or ""),
            reference=str(data.get("reference") or reference),
        )

    def verify_transaction(self, reference: str) -> VerifiedTransaction:
        """Ask Paystack whether a reference was really paid.

        A 404 from Paystack means "I have no transaction with that reference",
        which is a different situation from "Paystack is down" and gets a
        different message. Telling a customer their payment failed when the
        provider is merely unreachable is how people pay twice.
        """
        body = self._get(f"/transaction/verify/{reference}", params={})
        data = body.get("data") or {}
        status = str(data.get("status") or "").lower()

        return VerifiedTransaction(
            reference=str(data.get("reference") or reference),
            status=status,
            amount_minor=int(data.get("amount") or 0),
            currency=str(data.get("currency") or "").upper(),
            paid=status == "success",
            channel=_as_str(data.get("channel")),
            customer_code=_as_str(data.get("customer", {}).get("customer_code"))
            if isinstance(data.get("customer"), dict)
            else None,
            customer_email=_as_str(data.get("customer", {}).get("email"))
            if isinstance(data.get("customer"), dict)
            else None,
            paid_at=_as_str(data.get("paid_at")),
            failure_reason=_as_str(data.get("gateway_response"))
            if not status == "success"
            else None,
        )

    def fetch_customer(self, customer_code: str) -> dict:
        """Read a Paystack customer record.

        Used when a ``subscription.create`` event arrives before Sauti has seen
        a payment for that customer, so the email can be filled in from the
        provider rather than trusted from the event body.
        """
        body = self._get(f"/customer/{customer_code}", params={})
        return body.get("data") or {}


def _as_str(value: Any) -> str | None:
    """Coerce to a non-empty string, or None. Truncated to column width."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:255]


__all__ = [
    "PaystackError",
    "PaystackDisabledError",
    "PaystackClient",
    "InitializedTransaction",
    "VerifiedTransaction",
    "compute_signature",
    "verify_signature",
    "paystack_mode",
    "is_test_mode",
    "assert_paystack_mode",
    "paystack_public_key",
    "web_url",
]
