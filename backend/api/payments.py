"""Payment and subscription endpoints.

Five routes:

    POST /api/payments/vendor/token    issue a vendor token
    POST /api/payments/initialize      open a Paystack checkout
    GET  /api/payments/verify/<ref>    verify a transaction and activate
    POST /api/payments/webhook         Paystack webhook receiver
    GET  /api/subscriptions/status     the vendor's current state

Error mapping is uniform across all of them, so the frontend has one shape to
handle:

    400 invalid payment request      401 authentication required
    403 forbidden / bad webhook sig  404 transaction not found
    409 duplicate or amount mismatch 429 too many payment attempts
    500/502/503 payment service temporarily unavailable

No handler in this module ever puts a Paystack response, a stack trace or a
credential into a body. Provider failures come back through
:class:`~backend.payments.paystack.PaystackError`, whose ``user_message`` was
written for a browser; the upstream ``detail`` goes to the log only.
"""
from __future__ import annotations

import logging
import re

from flask import Blueprint, g, jsonify, request
from pydantic import ValidationError

from ..db import db
from ..payments.auth import (
    VENDOR_TOKEN_HEADER,
    VendorSecretError,
    authorize_token_issuance,
    issue_vendor_token,
    require_vendor,
)
from ..payments.models import PAYMENT_PENDING, Payment, utcnow
from ..payments.paystack import (
    PaystackClient,
    PaystackError,
    is_test_mode,
    paystack_public_key,
    verify_signature,
)
from ..payments.service import (
    PaymentServiceError,
    RetryableWebhookError,
    ensure_vendor_plan,
    initialize_payment,
    process_event,
    record_event,
    resolve_vendor_identity,
    subscription_status,
    verify_payment,
)
from ..schemas.payments import PaymentInitializeRequest, VendorTokenRequest
from .limiter import limiter

logger = logging.getLogger(__name__)

payments_bp = Blueprint("payments", __name__)

#: A reference Sauti generates looks like `sauti_<32 hex>`. Validating the shape
#: before it reaches the database or Paystack keeps a 10KB path segment from
#: becoming a slow chain of string comparisons in an outbound request.
_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,120}$")

#: Cap on the webhook body. Paystack's largest real event is a few kilobytes;
#: this leaves generous headroom while refusing to buffer something enormous.
_WEBHOOK_MAX_BYTES = 512 * 1024


def _rate_limit(key: str):
    """Read a payment rate limit from config, per request.

    A lambda rather than a string so the limit follows configuration changes
    without a restart, matching how :mod:`backend.api.chat` does it.
    """
    from flask import current_app

    return limiter.limit(lambda: current_app.config.get(key, "60 per minute"))


def _validation_error(exc: ValidationError) -> tuple:
    """Turn a Pydantic failure into the backend's flat error shape."""
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first.get("loc", ())) or "body"
    return jsonify({"error": f"{field}: {first.get('msg')}"}), 400


def _callback_url() -> str | None:
    """Where Paystack should send the customer back to.

    Built only from ``PUBLIC_SITE_URL``, never from the ``Origin`` header: that
    is attacker-controlled, and a callback URL derived from it would send a
    paying customer to a page on someone else's domain.

    Returns None when the operator has not set ``PUBLIC_SITE_URL``. Paystack then
    returns the customer to its own page, which is survivable — the subscription
    still activates from the webhook — but the customer lands somewhere unhelpful.
    The status endpoint reports the value so it can be compared against what the
    Paystack dashboard actually has configured.
    """
    from flask import current_app

    configured = (current_app.config.get("PUBLIC_SITE_URL") or "").strip()
    if not configured:
        return None
    return f"{configured.rstrip('/')}/payment/callback"


# ---------------------------------------------------------------------------
# Vendor authentication
# ---------------------------------------------------------------------------


@payments_bp.route("/payments/vendor/token", methods=["POST"])
@_rate_limit("PAYSTACK_TOKEN_RATE_LIMIT")
def vendor_token():
    """Issue a vendor token.

    Body: {"user_id": "...", "vendor_id": "...", "email": "...", "secret": "..."}

    See :mod:`backend.payments.auth` for what this token does and does not
    prove. The short version: it stops a caller reading or writing another
    vendor's billing records, and it stops forged vendor ids. It is not a
    password, because Sauti has no passwords.

    ``secret`` authorises issuance. The first request for a given ``user_id`` has
    none, is issued one, and receives it in ``vendorSecret`` — the only time it is
    ever sent. Every later request must present it, so learning an established
    ``user_id`` is no longer enough to mint tokens for it.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be valid JSON"}), 400

    try:
        parsed = VendorTokenRequest(**data)
    except ValidationError as exc:
        return _validation_error(exc)

    try:
        user, vendor = resolve_vendor_identity(parsed.user_id, parsed.vendor_id)
    except PaymentServiceError as exc:
        return jsonify({"error": exc.message}), exc.status

    # After resolve_vendor_identity, so the row exists and can carry the digest.
    try:
        secret, is_new = authorize_token_issuance(user.id, parsed.secret)
    except VendorSecretError as exc:
        db.session.rollback()
        return jsonify({"error": exc.message}), exc.status

    issued = issue_vendor_token(user.id, vendor.id)

    logger.info(
        "Issued a vendor token for vendor %s user %s (secret %s)",
        vendor.id,
        user.id,
        "created" if is_new else "presented",
    )

    response = {
        "token": issued["token"],
        "expiresAt": issued["expiresAt"],
        "expiresIn": issued["expiresIn"],
        "vendorId": vendor.id,
        "vendorName": vendor.business_name,
        "headerName": VENDOR_TOKEN_HEADER,
    }
    if is_new:
        # The one and only time this is transmitted. The server keeps only the
        # digest, so a browser that loses it cannot recover it and the user must
        # start again with a new user_id.
        response["vendorSecret"] = secret

    return jsonify(response)


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


@payments_bp.route("/payments/initialize", methods=["POST"])
@require_vendor
@_rate_limit("PAYSTACK_INIT_RATE_LIMIT")
def initialize():
    """Open a Paystack checkout for the authenticated vendor.

    The request body may carry an email for the receipt and a plan slug. It
    cannot carry an amount or a currency, and there is no parameter through
    which it could: the schema has no field for either, and the price is read
    from the ``plans`` row below the route. Changing the number in the browser's
    request body changes nothing, because the field does not exist to be read.

    Returns 200 with ``authorizationUrl``, ``accessCode`` and ``reference``. The
    secret key is not in any of them, and there is no code path that could put
    it there.
    """
    body = request.get_data(cache=True)
    if body.strip():
        # A non-empty body that is not JSON is a client bug, not an empty
        # request. Silently treating it as `{}` would start a real checkout from
        # a request the caller believed was malformed.
        try:
            data = request.get_json(silent=True)
        except Exception:  # noqa: BLE001 - malformed bodies must not 500
            data = None
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be valid JSON"}), 400
    else:
        # No body at all is fine: every field on the schema is optional.
        data = {}

    try:
        parsed = PaymentInitializeRequest(**data)
    except ValidationError as exc:
        return _validation_error(exc)

    principal = g.vendor_principal

    try:
        client = PaystackClient()
        result = initialize_payment(
            user_id=principal.user_id,
            vendor_id=principal.vendor_id,
            client=client,
            email=parsed.email,
            callback_url=_callback_url(),
        )
    except PaymentServiceError as exc:
        return jsonify({"error": exc.message}), exc.status
    except PaystackError as exc:
        logger.error("Paystack initialize failed: %s", exc.detail)
        # Always 502, never exc.status. Paystack's own HTTP status is not this
        # API's contract: forwarding it would tell a browser that a provider
        # authentication failure is a Sauti authentication failure, and would
        # also mean an upstream 401 from a mis-set key surfaces to the customer
        # as "please sign in" against an endpoint they are already signed in to.
        return jsonify({"error": exc.user_message}), 502

    return jsonify(result)


@payments_bp.route("/payments/verify/<reference>", methods=["GET"])
@require_vendor
@_rate_limit("PAYSTACK_VERIFY_RATE_LIMIT")
def verify(reference: str):
    """Verify a transaction with Paystack and activate the subscription.

    This is the only thing that decides whether a payment succeeded. The frontend
    callback calls it and renders whatever it says; it never draws that
    conclusion itself, because a page that reads ``?status=success`` from the URL
    is a page anyone can open with a made-up query string.

    404 unknown reference, 403 someone else's payment, 409 amount or currency
    mismatch, 402 not paid.
    """
    candidate = (reference or "").strip()

    if not _REFERENCE_PATTERN.match(candidate):
        # Not a reference shape Sauti or Paystack could have produced. 404
        # rather than 400 so this cannot be used to probe the format.
        logger.info("Rejected a verification for a malformed reference")
        return jsonify({"error": "Transaction not found"}), 404

    principal = g.vendor_principal

    try:
        client = PaystackClient()
        result = verify_payment(
            candidate, vendor_id=principal.vendor_id, client=client
        )
    except PaymentServiceError as exc:
        return jsonify({"error": exc.message}), exc.status
    except PaystackError as exc:
        logger.error("Paystack verify failed: %s", exc.detail)
        # 502 for every upstream failure. See the note in initialize().
        return jsonify({"error": exc.user_message}), 502

    return jsonify(result)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


@payments_bp.route("/payments/webhook", methods=["POST"])
def webhook():
    """Receive a Paystack webhook.

    This is the route Paystack's dashboard posts to, so it is public and
    unauthenticated by design. Authenticity comes from the
    ``x-paystack-signature`` header instead: an HMAC-SHA512 of the raw body keyed
    with the secret key. A request without a valid signature is rejected with
    403 before its body is looked at.

    Deliberately not rate limited. The limiter keys on IP, and Paystack delivers
    from a shared pool, so a limit here would reject legitimate events. The
    per-event cost is one indexed insert.

    Idempotency is the point of this handler: Paystack retries, so the same
    event will arrive more than once, and it must never apply twice. See
    :func:`backend.payments.service.record_event`.
    """
    logger.info("Webhook received")

    # Size is checked before the body is read, not after. ``get_data()`` buffers
    # the entire request into memory, so validating the length afterwards
    # "protects" nothing: the allocation has already happened. A single oversized
    # POST can therefore pin a worker with its body resident regardless of the
    # limit below. ``content_length`` is declared by the client and checked first
    # so the common case never reaches the read at all.
    declared_length = request.content_length
    if declared_length is not None and declared_length > _WEBHOOK_MAX_BYTES:
        logger.warning(
            "Rejected an oversized webhook body before reading it: %s bytes",
            declared_length,
        )
        return jsonify({"error": "Invalid webhook signature"}), 403

    # The signature covers the exact bytes on the wire, so the body is read
    # before anything parses it. Reading it again later returns the cached copy,
    # which is why the cache flag matters.
    raw_body = request.get_data(cache=True, as_text=False)

    # A chunked request declares no length, so the real size is only known once
    # the body has been read. This second check still matters: it is the only
    # thing standing between a lying or absent Content-Length and an unbounded
    # buffer. It is defence in depth, not the primary limit.
    if len(raw_body) > _WEBHOOK_MAX_BYTES:
        logger.warning(
            "Rejected an oversized webhook body: %s bytes", len(raw_body)
        )
        return jsonify({"error": "Invalid webhook signature"}), 403

    signature = request.headers.get("x-paystack-signature")

    if not signature:
        logger.warning("Rejected a webhook with no signature header")
        return jsonify({"error": "Invalid webhook signature"}), 403

    if not verify_signature(raw_body, signature):
        logger.warning("Rejected a webhook with an invalid signature")
        return jsonify({"error": "Invalid webhook signature"}), 403

    logger.info("Webhook signature verified")

    event = request.get_json(silent=True, force=False)
    if not isinstance(event, dict):
        # Signature was valid but the body is not the event object. Nothing to
        # act on; acknowledge rather than retry an unparseable payload.
        logger.warning("Webhook signature verified but the body was not an event")
        return jsonify({"status": True, "message": "ignored"}), 200

    try:
        stored, duplicate = record_event(event)
    except Exception:  # noqa: BLE001 - the event must be recorded or surfaced
        logger.exception("Failed to record a webhook event")
        db.session.rollback()
        # 500 so Paystack retries: the event was not durably recorded, and
        # losing a charge.success means losing a subscription.
        return jsonify({"error": "Payment service temporarily unavailable"}), 500

    if duplicate:
        # 200, not an error. The event is already applied and a non-2xx would
        # only make Paystack retry something that can never change.
        logger.info("Duplicate webhook ignored: event=%s", stored.event_type)
        return jsonify({"status": True, "message": "duplicate ignored"}), 200

    try:
        client = PaystackClient()
    except PaystackError:
        # The signature verified, so the key is present and correct — this is
        # almost certainly the live-key guard. Handle without an outbound call
        # rather than failing the event.
        logger.error("Webhook could not construct a Paystack client")
        client = None

    try:
        outcome = process_event(stored, client)
    except RetryableWebhookError:
        # Paystack could not be asked for the facts. The event is recorded and
        # still unprocessed, and it is left that way on purpose.
        #
        # Marking it processed here would be the worst outcome available: Paystack
        # would stop retrying, the row would claim to be handled, and the
        # subscription would stay inactive with the customer already charged.
        # Leaving processed=False costs nothing — ``record_event`` reports an
        # unprocessed row as not-duplicate, so the redelivery is processed for
        # real — and the customer is activated as soon as Paystack retries.
        logger.exception(
            "Webhook could not be processed for now: event=%s. "
            "Leaving it unprocessed so Paystack can retry.",
            stored.event_type,
        )
        db.session.rollback()
        return jsonify({"error": "Payment service temporarily unavailable"}), 503

    # The event is applied. Refusals and handled-and-ignored outcomes are marked
    # processed: re-running them would be the duplicate this table exists to
    # prevent, and neither will become valid on a retry.
    stored.processed = True
    stored.processed_at = utcnow()
    db.session.commit()

    logger.info("Webhook processed: event=%s outcome=%s", stored.event_type, outcome)

    return jsonify({"status": True, "message": "ok"}), 200


@payments_bp.route("/payments/config", methods=["GET"])
@require_vendor
def callback():
    """Payment configuration the frontend needs to render checkout.

    Returns the public key, the callback URL this deployment computes, and
    whether it is in test mode — all three so an operator can confirm the
    Paystack dashboard matches. Never the secret key: there is no field in the
    response that could hold one.
    """
    return jsonify(
        {
            "publicKey": paystack_public_key(),
            "callbackUrl": _callback_url(),
            "mode": "test" if is_test_mode() else "live",
        }
    )


# ---------------------------------------------------------------------------
# Subscription status
# ---------------------------------------------------------------------------


@payments_bp.route("/subscriptions/status", methods=["GET"])
@require_vendor
@_rate_limit("PAYSTACK_VERIFY_RATE_LIMIT")
def status():
    """The authenticated vendor's subscription state.

    The endpoint the frontend feature gate reads. Creates nothing and activates
    nothing — a vendor who has never paid reads ``inactive`` forever until money
    actually moves, which is the point.

    ``mode`` reports ``test`` or ``live`` so the UI can label itself. A test
    badge on the pricing screen is the cheapest guard against someone handing a
    test build to a real vendor believing real money is involved.
    """
    principal = g.vendor_principal

    plan = ensure_vendor_plan()
    payload = subscription_status(principal.vendor_id, principal.user_id)

    # Only ever the pk_ value; see paystack_public_key for why this is safe.
    payload["publicKey"] = paystack_public_key()
    payload["mode"] = "test" if is_test_mode() else payload.get("mode", "test")
    payload["callbackUrl"] = _callback_url()

    logger.info(
        "Subscription status read: vendor=%s status=%s",
        principal.vendor_id,
        payload["status"],
    )

    return jsonify(payload)


@payments_bp.route("/payments/pending", methods=["GET"])
@require_vendor
def pending_payment():
    """The vendor's most recent un-paid checkout, if it is still usable.

    Lets the callback page and the subscription page recover from a closed
    browser tab: the customer who paid but never came back still has a
    reference in the database, and asking for it beats generating a second one.
    """
    principal = g.vendor_principal

    payment = (
        Payment.query.filter_by(
            vendor_id=principal.vendor_id, status=PAYMENT_PENDING
        )
        .order_by(Payment.created_at.desc())
        .first()
    )

    if payment is None:
        return jsonify({"reference": None})

    return jsonify({"reference": payment.reference, "createdAt": payment.created_at.isoformat() if payment.created_at else None})


__all__ = ["payments_bp"]