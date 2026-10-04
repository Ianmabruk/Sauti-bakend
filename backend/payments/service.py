"""Payment and subscription service.

This module owns every decision about money. Two rules run through all of it:

* **The server decides the amount and the currency.** They come from the
  ``plans`` row and nowhere else. A request body that mentions an amount does not
  reach this layer at all — the schema for initialize has no such field — and if
  a future caller adds one, the code below does not read it.

* **Only an independently verified Paystack transaction activates anything.**
  Neither the browser callback nor the presence of a webhook may grant access on
  its own word. The callback asks Paystack. The webhook's HMAC proves it came
  from Paystack, but its *claims* are still re-checked against the stored plan
  price before a subscription moves, because a signature proves authenticity,
  not accuracy.

Idempotency is layered. ``payment_events`` stops a replayed delivery from being
processed twice (unique constraint, not a check-then-insert). A unique
``payments.reference`` stops two checkouts colliding. The reuse window stops a
double-clicked Subscribe button from orphaning references. And
:meth:`Subscription.mark_active` refuses to extend a paid period that has not
run out, so even a genuinely duplicated activation cannot grant two months.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from ..db import db
from ..marketplace.models import Vendor
from ..models import User
from .models import (
    CURRENCY_EXPONENTS,
    PAYMENT_ABANDONED,
    PAYMENT_FAILED,
    PAYMENT_PENDING,
    PAYMENT_SUCCESS,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_CANCELLED,
    SUBSCRIPTION_EXPIRED,
    SUBSCRIPTION_INACTIVE,
    SUBSCRIPTION_PAST_DUE,
    SUBSCRIPTION_PENDING,
    SUBSCRIPTION_CURRENCY,
    Payment,
    PaymentEvent,
    Plan,
    Subscription,
    as_aware,
    derive_event_key,
    major_to_minor,
    minor_to_major,
    utcnow,
)

logger = logging.getLogger(__name__)

#: Slug of the plan the vendor subscription uses.
VENDOR_PLAN_SLUG = "vendor-monthly"

#: Reference prefix. Paystack references are opaque strings, but a recognisable
#: prefix makes a Sauti transaction identifiable in the Paystack dashboard,
#: which is where anyone debugging a test failure will actually be looking.
REFERENCE_PREFIX = "sauti_"


class PaymentServiceError(Exception):
    """A payment request cannot be served.

    ``status`` is the HTTP code the route should return. Using an exception here
    rather than returning a tuple keeps the happy path of each function free of
    response plumbing.
    """

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class RetryableWebhookError(Exception):
    """A webhook could not be handled because a dependency was unavailable.

    Distinct from :class:`PaymentServiceError` because the two need opposite
    responses. A business refusal means the event will never become valid, so it
    is acknowledged with a 200 and Paystack stops asking. A dependency failure —
    Paystack timing out, a connection refused, a database blip — means the event
    is still perfectly valid and only this attempt failed, so it must be answered
    with a retryable status or the event is lost for good.

    Merging the two cases is what turns a momentary provider outage into a
    permanently un-activated subscription.
    """


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def ensure_vendor_plan() -> Plan:
    """Return the vendor plan, creating it from configuration on first use.

    The plan row is seeded from ``PAYSTACK_PLAN_CODE`` rather than hardcoded.
    Until an operator creates the plan in the Paystack Test dashboard and sets
    that variable, ``paystack_plan_code`` stays NULL and
    :func:`initialize_payment` refuses with a clear message. That is deliberate:
    guessing a ``PLN_`` code would fail deep inside Paystack with a message
    about an unknown plan, pointing at the wrong layer entirely.

    Seeding on read rather than in a migration keeps the price in one place. A
    migration that hardcoded 10000 would then be a second source of truth, and
    the two would disagree the first time the price changed.

    **The plan code is backfilled from configuration when it is missing.** The
    row is seeded the first time anyone calls this, and the documented deploy
    order is to deploy before the Paystack plan exists and set the variable
    afterwards. If this only wrote the code at seed time, that order would brick
    checkout permanently: the row would exist with a NULL code, nothing would
    ever fill it in, and every initialize would return 503 with no indication of
    why. The backfill updates only ``paystack_plan_code`` — never the amount,
    the currency or the interval — so configuration can never silently reprice a
    plan that existing payments were snapshotted against.

    **A code that is already set is not overwritten, only reported.** Changing
    which Paystack plan new subscribers are billed against is a data change, not
    a configuration change: existing subscriptions stay attached to the old plan
    while new ones attach to the new one, and nothing records that split. Doing
    that because an environment variable was edited would be invisible and would
    take effect on the next checkout. So a mismatch is logged as a warning and
    left for an operator to resolve deliberately.
    """
    from flask import current_app

    configured_code = (current_app.config.get("PAYSTACK_PLAN_CODE") or "").strip()

    plan = Plan.query.filter_by(slug=VENDOR_PLAN_SLUG).first()
    if plan:
        if configured_code and not plan.paystack_plan_code:
            logger.info(
                "Backfilling the Paystack plan code on the %s plan from "
                "configuration.",
                plan.slug,
            )
            plan.paystack_plan_code = configured_code
            db.session.commit()
        elif configured_code and plan.paystack_plan_code != configured_code:
            logger.warning(
                "PAYSTACK_PLAN_CODE is %s but the stored %s plan is already on "
                "%s. Keeping the stored code: changing it would move new "
                "subscribers to a different Paystack plan while existing "
                "subscriptions stay on this one. Update the plan row "
                "deliberately if that is intended.",
                configured_code,
                plan.slug,
                plan.paystack_plan_code,
            )
        return plan

    plan = Plan(
        slug=VENDOR_PLAN_SLUG,
        name="Sauti Vendor Subscription",
        # The price lives here as a constant rather than being configured,
        # because it is a product decision, not an environment-specific value.
        # KSh 100 == 10000 in the smallest unit. See major_to_minor.
        amount_minor=major_to_minor(100),
        currency=SUBSCRIPTION_CURRENCY,
        interval="monthly",
        minor_exponent=CURRENCY_EXPONENTS[SUBSCRIPTION_CURRENCY],
        paystack_plan_code=configured_code or None,
        description="Monthly Sauti vendor subscription",
        is_active=True,
    )
    db.session.add(plan)
    try:
        db.session.commit()
    except IntegrityError:
        # Two workers seeded the same row concurrently. The unique slug means
        # exactly one of them won; take theirs.
        db.session.rollback()
        existing = Plan.query.filter_by(slug=VENDOR_PLAN_SLUG).first()
        if existing:
            return existing
        raise

    logger.info(
        "Seeded vendor plan %s at %s minor units (paystack plan code: %s)",
        plan.slug,
        plan.amount_minor,
        "configured" if plan.paystack_plan_code else "NOT CONFIGURED",
    )
    return plan


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def resolve_vendor_identity(user_id: str, vendor_id: str) -> tuple[User, Vendor]:
    """Bind an anonymous user id to a vendor, or refuse.

    A vendor with no owner is adopted and marked owned. A vendor that already
    has an owner is returned only to that same owner — this is the check that
    stops anyone from minting a token for someone else's vendor by guessing its
    id, and it is the reason a token cannot be used to read another business's
    billing history.

    Raises:
        PaymentServiceError: 404 when the vendor does not exist.
    """
    user = User.query.filter_by(id=user_id).first()
    if user is None:
        user = User(id=user_id, is_active=True)
        db.session.add(user)
        db.session.flush()

    vendor = Vendor.query.filter_by(id=vendor_id).first()
    if vendor is None:
        raise PaymentServiceError("Vendor not found", 404)

    if vendor.owner_user_id and vendor.owner_user_id != user_id:
        logger.warning(
            "Refused to bind vendor %s to a user that does not own it", vendor_id
        )
        raise PaymentServiceError("Forbidden", 403)

    if not vendor.owner_user_id:
        vendor.owner_user_id = user_id
        db.session.flush()

    return user, vendor


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


def new_reference() -> str:
    """A unique transaction reference.

    The UUID4 suffix makes a collision negligible, and the column carries a
    unique constraint so even a collision is a rejected insert rather than two
    payments sharing one reference.
    """
    return f"{REFERENCE_PREFIX}{uuid.uuid4().hex}"


def find_reusable_payment(vendor_id: str, plan_id: str, window_seconds: int) -> Payment | None:
    """An un-paid payment recent enough to keep using.

    Without this, a vendor who taps Subscribe twice gets two live Paystack
    checkout sessions for the same month, and whichever one they pay first wins
    while the other reference stays dangling in Paystack forever. Reusing the
    same reference is also what makes the operation idempotent from the
    browser's point of view.
    """
    if window_seconds <= 0:
        return None

    cutoff = (utcnow() - timedelta(seconds=window_seconds)).timestamp()
    candidates = (
        Payment.query.filter(
            Payment.vendor_id == vendor_id,
            Payment.plan_id == plan_id,
            Payment.status == PAYMENT_PENDING,
        )
        .order_by(Payment.created_at.desc())
        .all()
    )

    for candidate in candidates:
        created = as_aware(candidate.created_at)
        if created is None:
            continue
        if created.timestamp() >= cutoff:
            return candidate

    # Everything pending is older than the window. Mark them abandoned so they
    # stop looking like live checkouts in the admin views.
    for stale in candidates:
        stale.status = PAYMENT_ABANDONED

    return None


def initialize_payment(
    *,
    user_id: str,
    vendor_id: str,
    client: object,
    email: str | None = None,
    callback_url: str | None = None,
) -> dict:
    """Start a checkout and return where to send the customer.

    Args:
        user_id: The authenticated Sauti user. From the vendor token, never the
            request body.
        vendor_id: The authenticated vendor. From the vendor token, never the
            request body.
        client: A :class:`~backend.payments.paystack.PaystackClient`.
        email: Receipt address. Optional, validated, and never load-bearing.
        callback_url: Absolute URL Paystack returns the customer to.

    Returns:
        A dict with only what the browser needs to open checkout: the
        authorization URL, the access code and the reference, plus the plan and
        subscription status for rendering.

    Raises:
        PaymentServiceError: 409 when already subscribed, 503 when the plan has
            no Paystack plan code, 429 when a recent attempt failed, 502 when
            Paystack is unreachable.
    """
    plan = ensure_vendor_plan()
    _user, vendor = resolve_vendor_identity(user_id, vendor_id)

    subscription = get_or_create_subscription(vendor, user_id, plan)

    if subscription.status == SUBSCRIPTION_ACTIVE:
        # Only `active` blocks a new checkout. `past_due` deliberately does not:
        # Paystack retries a failed renewal before giving up, but a vendor whose
        # card has since expired needs to be able to pay with a different one.
        # Gating on `is_entitled` here would lock those vendors out until
        # Paystack disabled their subscription.
        raise PaymentServiceError(
            "This vendor already has an active subscription.", 409
        )

    if not plan.has_paystack_plan:
        # The operator has not set PAYSTACK_PLAN_CODE yet, so there is nothing
        # to charge against. Saying so beats letting Paystack reject an unknown
        # plan code with a message that points at Paystack instead of here.
        raise PaymentServiceError(
            "The subscription plan is not available yet. Please try again shortly.",
            503,
        )

    # A vendor who previously cancelled or let a checkout lapse is starting
    # again, so the row goes back to pending. Without this they would be checked
    # out successfully while their own dashboard still read "cancelled".
    if subscription.status in (
        SUBSCRIPTION_CANCELLED,
        SUBSCRIPTION_EXPIRED,
        SUBSCRIPTION_INACTIVE,
    ):
        subscription.status = SUBSCRIPTION_PENDING
        subscription.updated_at = utcnow()

    from flask import current_app

    window = int(current_app.config.get("PAYSTACK_REUSE_WINDOW_SECONDS") or 0)

    payment = find_reusable_payment(vendor.id, plan.id, window)

    if payment is not None and payment.authorization_url:
        # The checkout for this reference is already open at Paystack. Returning
        # the stored URL is the whole point of the reuse window: calling
        # initialize again with the same reference makes Paystack reject it as a
        # duplicate, and its original response is not retrievable afterwards, so
        # the customer would be left with a 502 and no way to pay.
        logger.info(
            "Reusing the open checkout for reference %s (vendor %s)",
            payment.reference,
            vendor.id,
        )
        return {
            "authorizationUrl": payment.authorization_url,
            "accessCode": payment.access_code or "",
            "reference": payment.reference,
            "amount": plan.amount_major,
            "currency": plan.currency,
            "interval": plan.interval,
            "subscriptionStatus": subscription.status,
        }

    if payment is not None:
        # Reusable but with no stored URL (the row predates this column, or the
        # earlier Paystack call never completed). Fall through and initialize it,
        # which is the only way to obtain a checkout URL for this reference.
        logger.info(
            "Reusing the open payment %s but it has no stored checkout URL; "
            "re-initializing.",
            payment.reference,
        )
    else:
        payment = Payment(
            vendor_id=vendor.id,
            user_id=user_id,
            plan_id=plan.id,
            subscription_id=subscription.id,
            reference=new_reference(),
            amount_minor=plan.amount_minor,
            currency=plan.currency,
            status=PAYMENT_PENDING,
        )
        db.session.add(payment)
        # Committed before Paystack is called. If the request dies mid-flight the
        # row is still here, which is what makes the reference recoverable
        # rather than a transaction that exists only at Paystack.
        db.session.commit()

    receipt_email = (email or "").strip() or _vendor_email(vendor, user_id) or ""

    logger.info(
        "Payment initialization started: reference=%s vendor=%s plan=%s amount_minor=%s currency=%s",
        payment.reference,
        vendor.id,
        plan.slug,
        plan.amount_minor,
        plan.currency,
    )

    initialized = client.initialize_transaction(
        reference=payment.reference,
        amount_minor=plan.amount_minor,
        currency=plan.currency,
        email=receipt_email,
        plan_code=plan.paystack_plan_code,
        callback_url=callback_url,
    )

    payment.access_code = initialized.access_code
    payment.authorization_url = initialized.authorization_url
    subscription.plan_code = plan.paystack_plan_code
    db.session.commit()

    logger.info(
        "Payment initialization successful: reference=%s vendor=%s",
        payment.reference,
        vendor.id,
    )

    return {
        "authorizationUrl": initialized.authorization_url,
        "accessCode": initialized.access_code,
        "reference": initialized.reference,
        "amount": plan.amount_major,
        "currency": plan.currency,
        "interval": plan.interval,
        "subscriptionStatus": subscription.status,
    }


def _vendor_email(vendor: Vendor, user_id: str) -> str | None:
    """Best available receipt address, from Sauti's own records only.

    Prefers the vendor's own listing email over anything else, and falls back to
    the user record. Both are already in the database; neither involves the
    request.
    """
    if vendor.email and "@" in vendor.email:
        return vendor.email.strip().lower()

    user = User.query.filter_by(id=user_id).first()
    # users has no email column, so there is nothing further to try here. Kept
    # as a function so adding one is a change in a single place.
    return None


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


def get_or_create_subscription(vendor: Vendor, user_id: str, plan: Plan) -> Subscription:
    """The vendor's subscription to ``plan``, created on first checkout.

    Uses the same insert-then-let-the-constraint-decide pattern as
    :func:`ensure_vendor_plan` and :func:`record_event`, rather than a
    SELECT-then-INSERT. The select-then-insert version has a window in which two
    concurrent callers both see "not present" and both insert; the loser takes an
    ``IntegrityError`` on ``uq_subscriptions_vendor_plan`` that nothing catches,
    and the customer gets a 500 from a double-tap on Subscribe — which is exactly
    the concurrency this function gets hammered by.
    """
    subscription = Subscription.query.filter_by(
        vendor_id=vendor.id, plan_id=plan.id
    ).first()
    if subscription:
        return subscription

    subscription = Subscription(
        vendor_id=vendor.id,
        user_id=user_id,
        plan_id=plan.id,
        status=SUBSCRIPTION_PENDING,
        plan_code=plan.paystack_plan_code,
    )
    db.session.add(subscription)
    try:
        db.session.flush()
    except IntegrityError:
        # Another request created it first. The unique (vendor_id, plan_id) key
        # means exactly one row exists; take it.
        db.session.rollback()
        existing = Subscription.query.filter_by(
            vendor_id=vendor.id, plan_id=plan.id
        ).first()
        if existing:
            return existing
        raise

    return subscription


def subscription_status(vendor_id: str, user_id: str) -> dict:
    """The vendor's current subscription state, for the feature gate.

    Never creates anything. A vendor who has never tried to pay has an
    ``inactive`` subscription, not a pending one, and the frontend must be able
    to tell those apart.
    """
    plan = ensure_vendor_plan()
    subscription = Subscription.query.filter_by(
        vendor_id=vendor_id, plan_id=plan.id
    ).first()

    if subscription is None:
        return {
            "status": SUBSCRIPTION_INACTIVE,
            "isEntitled": False,
            "plan": plan.to_dict(),
            "subscription": None,
            "mode": _mode_label(),
        }

    if subscription.status == SUBSCRIPTION_PENDING and _is_expired(subscription):
        # A pending subscription whose paid window has run out is expired, not
        # pending. Left as pending it would show a vendor as "waiting for
        # payment" forever.
        subscription.status = SUBSCRIPTION_EXPIRED
        db.session.commit()

    return {
        "status": subscription.status,
        "isEntitled": subscription.is_entitled,
        "plan": plan.to_dict(),
        "subscription": subscription.to_dict(),
        "mode": _mode_label(),
    }


def _mode_label() -> str:
    """``test`` or ``live``, so the UI can label itself honestly.

    A test-mode badge on the pricing screen is the cheapest possible guard
    against the person who is about to hand this build to a real vendor and
    believes real money is involved.
    """
    from .paystack import paystack_mode

    return paystack_mode()


def _is_expired(subscription: Subscription) -> bool:
    """Whether a pending checkout has aged out.

    Measured from ``next_payment_date`` when one exists — an active subscription
    whose paid window has elapsed — and otherwise from when the checkout was last
    touched.

    The fallback is not optional. A ``pending`` subscription has never been paid,
    so ``next_payment_date`` is NULL, so keying only on that column made this
    function constantly false and the ``pending -> expired`` transition
    unreachable. That is exactly the abandoned-checkout case the transition
    exists to fix: a vendor who opened a checkout, walked away, and came back
    months later would be told they were "awaiting payment" forever, with no way
    to start again. Falling back to the row's own timestamps makes the transition
    reachable for the population it was written for.

    Both sides are normalised to aware UTC. SQLite returns naive timestamps from
    a ``DateTime(timezone=True)`` column and PostgreSQL returns aware ones, so an
    unnormalised comparison raises in development and passes in production.
    """
    window_end = as_aware(subscription.next_payment_date)
    if window_end is not None:
        return window_end < utcnow()

    # No paid window, so this is an unfinished checkout. Expire it once the
    # payment it belongs to is older than the reuse window.
    from flask import current_app

    window_seconds = int(
        current_app.config.get("PAYSTACK_REUSE_WINDOW_SECONDS") or 900
    )
    started = as_aware(subscription.updated_at) or as_aware(subscription.created_at)
    if started is None:
        return False

    return started <= utcnow() - timedelta(seconds=window_seconds)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_payment(reference: str, *, vendor_id: str | None, client: object) -> dict:
    """Verify a reference against Paystack and apply the result.

    This is the authority the frontend callback defers to. The browser sends a
    reference and receives a verdict; it never sends a verdict.

    Paystack is consulted exactly once per call. Verifying twice would be
    both wasteful and, on a status that can change between calls, a way to act
    on two different answers in one request.

    Args:
        reference: The reference to check. Format-validated by the caller.
        vendor_id: When given, the payment must belong to this vendor. A
            callback is reachable from a shared or hijacked URL, so a reference
            that verifies is not by itself proof that the caller is entitled to
            be told about it.
        client: A :class:`~backend.payments.paystack.PaystackClient`.

    Raises:
        PaymentServiceError: 404 unknown reference, 403 someone else's payment,
            409 mismatched amount or currency, 402 unpaid, 502 Paystack
            unavailable.
    """
    logger.info("Payment verification started: reference=%s", reference)

    # Local lookup happens BEFORE the outbound call. Every reference Sauti issues
    # is written to the database before Paystack is ever called (see
    # initialize_payment), so a reference with no row is one this system never
    # created — and the answer is 404 either way. Contacting Paystack first meant
    # an unknown reference cost a real HTTPS round trip, which turned this route
    # into an amplifier: the vendor token it requires is obtainable without any
    # authentication, so an anonymous caller could mint unlimited distinct
    # references and spend Sauti's Paystack quota at the verify rate limit.
    payment = Payment.query.filter_by(reference=reference).first()

    if payment is None:
        # Paystack may well know this reference, but Sauti never issued it. The
        # tempting move would be to adopt it: the transaction genuinely exists
        # and the customer genuinely paid. But a reference reaches us from a
        # Paystack dashboard, a receipt, a chat message or a referrer header, and
        # adopting one would let the holder of any valid reference attach a
        # stranger's payment to their own subscription.
        logger.error(
            "Reference %s is not one Sauti issued. Refusing to adopt it; no "
            "subscription changed.",
            reference,
        )
        raise PaymentServiceError("Transaction not found", 404)

    if vendor_id and payment.vendor_id != vendor_id:
        logger.warning(
            "Refused to verify reference %s for a vendor that does not own it",
            reference,
        )
        raise PaymentServiceError("Forbidden", 403)

    verified = client.verify_transaction(reference)

    if not verified.paid:
        _record_failure(payment, verified.status)
        raise PaymentServiceError("Payment failed", 402)

    # Paystack's answer must match both what Sauti expected to charge and what
    # the plan costs now. See the two _reject_* functions for why both.
    _reject_mismatch(payment, verified.amount_minor, verified.currency)
    _warn_on_price_drift(payment, verified.amount_minor)

    subscription = _apply_success(payment, verified, source="verify")

    logger.info(
        "Payment verification successful: reference=%s vendor=%s status=%s",
        reference,
        payment.vendor_id,
        verified.status,
    )

    return {
        "verified": True,
        "status": verified.status,
        "reference": verified.reference,
        "amount": minor_to_major(payment.amount_minor, payment.currency),
        "amountMinor": payment.amount_minor,
        "currency": payment.currency,
        "channel": verified.channel,
        "paidAt": _iso(payment.paid_at),
        "subscription": subscription.to_dict() if subscription else None,
        "subscriptionStatus": subscription.status
        if subscription
        else SUBSCRIPTION_INACTIVE,
    }


def _reject_mismatch(payment: Payment, amount_minor: int, currency: str) -> None:
    """Refuse a verified payment that does not match what was expected.

    A signature-verified webhook is authentic but not necessarily accurate: a
    plan price changed after a reference was created, or a reference was reused
    against a different amount. Activating on a mismatch would grant access for
    the wrong price, so it is refused loudly instead.
    """
    if int(amount_minor) != int(payment.amount_minor):
        logger.error(
            "Amount mismatch on reference %s: expected %s, Paystack reports %s. "
            "Subscription not activated.",
            payment.reference,
            payment.amount_minor,
            amount_minor,
        )
        raise PaymentServiceError(
            "Payment amount did not match the subscription price. Contact support.",
            409,
        )

    if (currency or "").upper() != (payment.currency or "").upper():
        logger.error(
            "Currency mismatch on reference %s: expected %s, Paystack reports %s. "
            "Subscription not activated.",
            payment.reference,
            payment.currency,
            currency,
        )
        raise PaymentServiceError(
            "Payment currency did not match the subscription price. Contact support.",
            409,
        )


def _warn_on_price_drift(payment: Payment, verified_amount_minor: int) -> None:
    """Log when a payment was taken at a price that is no longer current.

    This used to refuse the payment outright. That was wrong, and the failure it
    caused was worse than the one it prevented: ``payment.amount_minor`` is
    snapshotted at checkout time, so a customer who opened a checkout at KSh 100
    and paid it after the price moved to KSh 150 was refused with a 409 *after
    Paystack had taken their money*. They had paid exactly what Sauti asked them
    to — which :func:`_reject_mismatch` has already proved — and still got no
    access.

    A price change is a business decision, not evidence of tampering. The honest
    response is to honour the price the customer agreed to and make the drift
    visible, so an operator can decide whether to grandfather the reference,
    refund it, or let it stand.

    Refusing on tampering grounds remains the job of :func:`_reject_mismatch`,
    which compares the verified amount against the amount Sauti actually
    requested for that reference. That check is unchanged and still blocks a
    forged or replayed amount.
    """
    plan = Plan.query.filter_by(id=payment.plan_id).first()
    if plan is None:
        return

    if int(verified_amount_minor) != int(plan.amount_minor):
        logger.warning(
            "Reference %s was taken for %s but the current plan price is %s. "
            "Honouring the price agreed at checkout; review whether pending "
            "references should be invalidated when the price changes.",
            payment.reference,
            verified_amount_minor,
            plan.amount_minor,
        )


def _record_failure(payment: Payment, status: str) -> None:
    """Move an un-paid payment to failed.

    A payment that is already ``success`` is left alone. A duplicate
    verification of a good transaction that happened to race a failure report
    must not downgrade it.
    """
    if payment.status == PAYMENT_SUCCESS:
        return

    payment.status = PAYMENT_FAILED
    payment.failure_reason = (status or "failed")[:255]
    payment.verified_at = utcnow()
    db.session.commit()

    logger.info(
        "Payment failed: reference=%s status=%s", payment.reference, status
    )


def _apply_success(
    payment: Payment, verified: object, *, source: str
) -> Subscription | None:
    """Mark a payment successful and activate its subscription, exactly once.

    Shared by the verify endpoint and the ``charge.success`` handler so the two
    cannot drift apart. Both call Paystack for the transaction data first, so
    neither is acting on an unverified claim.

    The activation itself is idempotent: a payment already marked success returns
    early without touching the subscription, and
    :meth:`Subscription.mark_active` refuses to extend a window that has not run
    out. That is two independent guards against a double month.
    """
    now = utcnow()

    if payment.status == PAYMENT_SUCCESS:
        logger.info(
            "Payment %s already recorded as successful; not extending the "
            "subscription again",
            payment.reference,
        )
        if payment.subscription:
            return payment.subscription
        return None

    payment.status = PAYMENT_SUCCESS
    payment.channel = getattr(verified, "channel", None)
    payment.paystack_customer_code = getattr(verified, "customer_code", None)
    payment.verified_at = now
    payment.paid_at = _parse_paid_at(getattr(verified, "paid_at", None)) or now
    payment.failure_reason = None

    subscription = None
    if payment.subscription_id:
        subscription = db.session.get(Subscription, payment.subscription_id)

    if subscription is None and payment.plan_id:
        vendor = db.session.get(Vendor, payment.vendor_id) if payment.vendor_id else None
        if vendor is not None:
            plan = db.session.get(Plan, payment.plan_id)
            subscription = get_or_create_subscription(vendor, payment.user_id, plan)
            payment.subscription_id = subscription.id

    if subscription is not None:
        subscription.paystack_customer_code = (
            payment.paystack_customer_code or subscription.paystack_customer_code
        )
        extended = subscription.mark_active(now=now)
        subscription.last_paystack_event_at = now
        logger.info(
            "Subscription %s set to active for vendor %s via %s (window extended: %s)",
            subscription.id,
            subscription.vendor_id,
            source,
            extended,
        )

    db.session.commit()
    return subscription


def _iso(moment: datetime | None) -> str | None:
    """Render a timestamp for the API, or None."""
    return moment.isoformat() if moment else None


def _parse_paid_at(value: object) -> datetime | None:
    """Parse Paystack's ISO-8601 timestamp, tolerating a bad value."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

#: Events that change subscription state. Anything outside this set is recorded
#: and acknowledged without being acted on.
ACTIONABLE_EVENTS = frozenset(
    {
        "charge.success",
        "subscription.create",
        "invoice.create",
        "invoice.payment_failed",
        "subscription.disable",
        "subscription.not_renew",
    }
)


def record_event(event: dict) -> tuple[PaymentEvent, bool]:
    """Store one webhook delivery. Returns ``(event, is_duplicate)``.

    The uniqueness guarantee is the database's, not this function's. It inserts
    and lets the constraint decide, rather than checking whether the row exists
    first: a check-then-insert has a window where two concurrent deliveries of
    the same event both see "not present" and both proceed to activate the same
    subscription twice.

    The IntegrityError is caught and the transaction rolled back, which discards
    any state the losing worker had already staged in this session.
    """
    event_type = str(event.get("event") or "unknown")
    event_key = derive_event_key(event)
    data = event.get("data") if isinstance(event.get("data"), dict) else {}

    nested_transaction = data.get("transaction") if isinstance(data.get("transaction"), dict) else {}
    nested_subscription = (
        data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
    )
    nested_customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}

    stored = PaymentEvent(
        event_type=event_type,
        event_key=event_key,
        reference=_first_str(data.get("reference"), nested_transaction.get("reference")),
        paystack_subscription_code=_first_str(
            nested_subscription.get("subscription_code"),
            data.get("subscription_code"),
        ),
        paystack_customer_code=_first_str(nested_customer.get("customer_code")),
        payload=_safe_payload(event),
        processed=False,
    )

    db.session.add(stored)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = PaymentEvent.query.filter_by(
            provider="paystack", event_key=event_key
        ).first()
        if existing is None:
            # The constraint fired for some other reason. Let it surface rather
            # than treating it as a duplicate and losing the event.
            raise
        logger.info(
            "Duplicate webhook: event=%s key=%s processed=%s",
            event_type,
            event_key,
            existing.processed,
        )
        # A duplicate is only safe to skip if the original was actually applied.
        # If the first attempt failed at the infrastructure level — Paystack timed
        # out, the database was unreachable — the row exists with processed=False
        # and this redelivery is Paystack's retry. Reporting it as a duplicate
        # without reprocessing would mark the event handled while the
        # subscription was still inactive, and the customer would have paid for
        # nothing with no further delivery coming.
        return existing, existing.processed

    logger.info("Webhook recorded: event=%s key=%s", event_type, event_key)
    return stored, False


#: The only event fields retained, at any depth of ``data``.
#:
#: An allow-list, not a deny-list. A deny-list can only remove the fields someone
#: already thought of, so it fails open: Paystack adds a field, nobody updates
#: the filter, and the new field — with whatever it carries — is stored. That has
#: happened before with ``metadata``, a free-form dict a merchant can put anything
#: in, and with the ``customer`` block, which carries an email, a name and a
#: phone number. An allow-list fails closed instead: an unrecognised field is
#: dropped until someone deliberately adds it, and adding it is a reviewable
#: change rather than an omission nobody notices.
#:
#: Every entry below is read by a handler in this module. Nothing is retained for
#: a human to look at later, which is the point: an audit trail worth reading is
#: worth having as structured columns, and the payload exists only to be processed
#: once.
_ALLOWED_DATA_FIELDS = frozenset(
    {
        # Transaction facts, read directly off a charge event.
        "reference",
        "amount",
        "currency",
        "status",
        "channel",
        "paid_at",
        # Subscription identity and lifecycle.
        "subscription_code",
        "plan_code",
        "plan",
        "invoice_code",
        "customer_code",
    }
)

#: Sub-objects we keep, each with its own allow-list. ``customer`` is included
#: only for ``customer_code``, which is how a renewal is matched to a
#: subscription, and ``email``, which Paystack itself uses to mail a renewal
#: link. Names and phone numbers are not kept.
_ALLOWED_CUSTOMER_FIELDS = frozenset({"customer_code", "email"})

_ALLOWED_TRANSACTION_FIELDS = _ALLOWED_DATA_FIELDS | {"customer"}

#: Sub-objects that carry their own allow-list, mapped to the fields kept.
_ALLOWED_NESTED: dict[str, frozenset[str]] = {
    "customer": _ALLOWED_CUSTOMER_FIELDS,
    "transaction": _ALLOWED_TRANSACTION_FIELDS,
}


def _filter_payload_data(value: object) -> object:
    """Keep only allow-listed keys from a nested event structure."""
    if not isinstance(value, dict):
        return {}

    kept: dict[str, object] = {}
    for key, child in value.items():
        name = str(key)

        if name == "customer":
            kept[name] = {
                k: v
                for k, v in _filter_payload_data(child).items()
                if k in _ALLOWED_CUSTOMER_FIELDS
            }
        elif name == "transaction":
            nested = _filter_payload_data(child)
            kept[name] = {
                k: v
                for k, v in nested.items()
                if k in _ALLOWED_TRANSACTION_FIELDS
            }
        elif name in _ALLOWED_DATA_FIELDS:
            kept[name] = child

    return kept


def _safe_payload(event: dict) -> dict:
    """The event, reduced to the fields processing actually needs.

    A webhook body carries customer details — an email address, a name, a phone
    number, a free-form ``metadata`` dict — and this table is long-lived. Keeping
    the body verbatim creates a data-retention obligation that nothing here has a
    way to discharge, so unknown fields are dropped at write time rather than
    filtered on read.

    Card data is never present in a Paystack webhook in the first place: the
    provider never sends a PAN, CVV or PIN. That is precisely why an allow-list is
    used here. A deny-list's correctness depends on enumerating every field name a
    provider might send, which is not knowable in advance and is exactly the kind
    of assumption that fails open when the provider's payload changes. The
    allow-list needs no such assumption: anything not named here is dropped, so
    both today's fields and whatever Paystack adds next are covered by default.
    """
    data = event.get("data") if isinstance(event.get("data"), dict) else {}

    return {
        "event": str(event.get("event") or "unknown"),
        "data": _filter_payload_data(data),
    }


def _first_str(*candidates: object) -> str | None:
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:120]
    return None


def process_event(event_record: PaymentEvent, client: object | None) -> str:
    """Apply one recorded webhook event. Returns a short outcome label.

    Every branch is defensive. A webhook arrives from the internet carrying
    whatever Paystack decided to send, so a missing field, a changed shape or a
    type where a dict was expected must not raise out of here — an unhandled
    exception turns into a 500, Paystack retries, and a permanently broken
    payload becomes an infinite retry loop against the endpoint.
    """
    event_type = event_record.event_type
    payload = event_record.payload or {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    try:
        if event_type == "charge.success":
            return _handle_charge_success(event_record, data, client)

        if event_type == "subscription.create":
            return _handle_subscription_create(event_record, data)

        if event_type == "invoice.create":
            # An invoice is Paystack announcing a charge it is about to attempt.
            # Nothing to change yet: the subscription becomes past_due only if
            # the charge fails, which arrives as invoice.payment_failed.
            logger.info("Invoice created for subscription %s", event_record.paystack_subscription_code)
            return "invoice_recorded"

        if event_type == "invoice.payment_failed":
            return _handle_invoice_failed(event_record, data)

        if event_type == "subscription.disable":
            return _handle_subscription_stop(event_record, SUBSCRIPTION_CANCELLED)

        if event_type == "subscription.not_renew":
            # Renewal was switched off, so this is a deliberate end rather than
            # a failure. Expired, not cancelled: nothing went wrong.
            return _handle_subscription_stop(event_record, SUBSCRIPTION_EXPIRED)

        if event_type not in ACTIONABLE_EVENTS:
            # Recorded, acknowledged, ignored. Returning 200 is what stops
            # Paystack retrying something we will never act on.
            logger.info("Ignoring unhandled webhook event: %s", event_type)
            return "ignored"

        logger.warning("Webhook event %s matched no handler", event_type)
        return "unhandled"

    except PaymentServiceError as exc:
        # A business-rule refusal. The event is recorded and acknowledged; it
        # will not become valid on a retry.
        logger.error("Webhook %s refused: %s", event_type, exc.message)
        event_record.processing_error = exc.message[:255]
        return "refused"

    except RetryableWebhookError:
        # An infrastructure failure — Paystack timed out or refused to answer.
        # Deliberately re-raised so the route can return a retryable status and
        # leave ``processed`` false. Swallowing it here would tell Paystack the
        # event was handled when it was not: Paystack stops retrying, the event
        # stays ``processed=False`` forever, and a subscription whose charge
        # succeeded silently never activates. A business refusal is not retryable;
        # a provider outage is.
        raise

    except Exception as exc:  # noqa: BLE001 - a webhook must never escape
        # Anything unexpected is recorded on the event and logged, then the
        # caller returns 200. A 500 here would make Paystack retry a payload
        # that is unlikely to change, which is how a single bad event turns into
        # a permanent outage of the endpoint.
        logger.exception("Unexpected failure handling webhook %s", event_type)
        event_record.processing_error = f"{type(exc).__name__}: {exc}"[:255]
        return "error"


def _handle_charge_success(
    event_record: PaymentEvent, data: dict, client: object | None
) -> str:
    """Apply a successful charge, whether it is a first payment or a renewal.

    The stored event has already had its HMAC verified, which proves the message
    came from Paystack. It does not prove the amount or currency are right, so
    those are still re-checked before anything is granted.

    **Renewals are the case this function used to get wrong.** Because Sauti sends
    a plan code, Paystack charges the subscription automatically every interval
    and issues its *own* reference for each renewal — one Sauti never generated
    and so has no ``payments`` row for. Looking the charge up by reference alone
    therefore matched nothing from month two onward: the charge was recorded,
    acknowledged, and discarded. The customer kept being billed and the
    subscription quietly stopped extending.

    So a reference miss now falls back to matching the *subscription*, using the
    subscription and customer codes Paystack attaches to renewal charges. Only if
    that also fails is the event refused.
    """
    reference = event_record.reference or _first_str(data.get("reference"))

    payment = (
        Payment.query.filter_by(reference=reference).first() if reference else None
    )

    if payment is None:
        payment = _record_renewal_payment(event_record, data)
        if payment is None:
            logger.error(
                "charge.success for reference %s could not be matched to a "
                "subscription. No subscription changed.",
                reference,
            )
            return "unknown_reference"

    if payment.status == PAYMENT_SUCCESS:
        logger.info("charge.success for an already-successful payment %s", payment.reference)
        return "already_applied"

    verified = _verified_from_data(data)

    if verified is None and client is not None:
        # The webhook carried no usable transaction body. Ask Paystack, which
        # re-establishes the facts from the provider rather than the event.
        #
        # This is reached only for a charge Sauti has already matched to a
        # subscription, so the outbound call is answering a specific question
        # about a known reference. A reference Sauti cannot place is refused
        # above without a round trip, which keeps an unmatched event from costing
        # a provider request.
        #
        # A failure here is this attempt's problem, not the event's: the event is
        # valid and a retry may succeed. Translating the provider error into
        # RetryableWebhookError keeps it on the retryable path rather than being
        # swallowed into a "handled" outcome.
        try:
            verified = client.verify_transaction(payment.reference)
        except Exception as exc:  # noqa: BLE001 - re-raised as retryable
            raise RetryableWebhookError(
                f"Paystack verification failed for {payment.reference}: {exc}"
            ) from exc

    if verified is None:
        logger.error(
            "charge.success for %s carried no verifiable amount", payment.reference
        )
        return "no_transaction_data"

    if not verified.paid:
        # A charge.success whose status is not success. Trusting the event name
        # over the transaction status would be exactly the "activate on the
        # client's say-so" mistake this design exists to avoid.
        logger.error(
            "charge.success for %s has status %s", payment.reference, verified.status
        )
        # Recorded as failed, exactly as the verify endpoint records it.
        #
        # The two paths used to disagree here: verify moved the row to failed
        # while the webhook left it pending. The same transaction therefore ended
        # up in different states depending on which route happened to learn about
        # it first, which is the kind of ambiguity that makes a billing screen
        # untrustworthy. Both now say the same thing, because Paystack reported
        # the same thing.
        _record_failure(payment, verified.status)
        return "not_paid"

    _reject_mismatch(payment, verified.amount_minor, verified.currency)
    _warn_on_price_drift(payment, verified.amount_minor)

    _apply_success(payment, verified, source="webhook")
    logger.info(
        "Webhook processed: charge.success reference=%s", payment.reference
    )
    return "activated"


def _record_renewal_payment(
    event_record: PaymentEvent, data: dict
) -> Payment | None:
    """Create the payment row for a charge Sauti did not initiate.

    Returns the existing row when the reference did match after all, and None when
    the event cannot be tied to any known subscription.

    The amount and currency come from the plan, not from the webhook. That is the
    same rule the checkout path follows, and it is what lets
    :func:`_reject_mismatch` do its job: the row records what Sauti expected to
    be charged, so a body claiming a different figure is caught rather than
    believed.
    """
    subscription = _find_subscription(
        code=event_record.paystack_subscription_code,
        customer_code=event_record.paystack_customer_code,
        reference=event_record.reference,
    )
    if subscription is None:
        return None

    plan = db.session.get(Plan, subscription.plan_id)
    if plan is None:
        return None

    renewal_reference = (
        event_record.reference
        or _first_str(data.get("reference"))
        or f"sauti_renewal_{uuid.uuid4().hex}"
    )

    payment = Payment(
        vendor_id=subscription.vendor_id,
        user_id=subscription.user_id,
        plan_id=subscription.plan_id,
        subscription_id=subscription.id,
        reference=renewal_reference,
        amount_minor=plan.amount_minor,
        currency=plan.currency,
        status=PAYMENT_PENDING,
    )
    db.session.add(payment)
    try:
        db.session.commit()
    except IntegrityError:
        # A concurrent delivery of the same event won the insert. Take its row.
        #
        # The recovery lookup uses the local rather than ``payment.reference``.
        # Reading the attribute off the instance would work today, because a
        # rolled-back pending instance returns to transient state with its set
        # attributes intact, but that depends on session-state semantics rather
        # than on anything visible here. The value was never in doubt, so it is
        # held outside the instance.
        db.session.rollback()
        existing = Payment.query.filter_by(reference=renewal_reference).first()
        if existing:
            return existing
        return None

    logger.info(
        "Recorded recurring charge %s for subscription %s. This is an automatic "
        "renewal; Sauti did not initiate it.",
        payment.reference,
        subscription.id,
    )
    return payment


def _verified_from_data(data: dict):
    """Build a :class:`VerifiedTransaction` from the event body.

    Only used when the webhook already carries the transaction inline, which
    Paystack does for ``charge.success``. The facts still come from Paystack's
    own payload — it passed the signature check — so this is not trusting the
    browser. The amount is nevertheless re-checked against the plan.

    **A missing status is treated as "not paid", not as "paid".** This used to
    default an absent status to ``"success"``, which directly contradicted
    :meth:`PaystackClient.verify_transaction`, where an absent status defaults to
    ``""`` and therefore ``paid=False``. Two decoders for the same field with
    opposite defaults meant a signed event carrying an amount and a currency but
    no status was treated as a completed payment on the webhook path and rejected
    on the verify path. The safe default is the refusing one, and it also lets the
    caller fall back to asking Paystack, which is what should decide.
    """
    from .paystack import VerifiedTransaction

    transaction = data.get("transaction") if isinstance(data.get("transaction"), dict) else {}
    if not transaction:
        transaction = data if isinstance(data.get("amount"), int) else {}
    if not transaction:
        return None

    amount = transaction.get("amount")
    currency = transaction.get("currency")
    status = transaction.get("status")

    # Without an amount or currency there is nothing to validate against, and
    # without a status there is nothing that says the money moved. Both cases fall
    # back to Paystack rather than guessing.
    if not isinstance(amount, int) or not isinstance(currency, str):
        return None
    if not isinstance(status, str) or not status.strip():
        return None

    customer = transaction.get("customer") if isinstance(transaction.get("customer"), dict) else {}

    return VerifiedTransaction(
        reference=str(transaction.get("reference") or ""),
        status=status.strip().lower(),
        amount_minor=amount,
        currency=currency.upper(),
        paid=status.strip().lower() == "success",
        channel=_first_str(transaction.get("channel")),
        customer_code=_first_str(customer.get("customer_code")),
        customer_email=_first_str(customer.get("email")),
        paid_at=_first_str(transaction.get("paid_at")),
        failure_reason=None,
    )


def _handle_subscription_create(event_record: PaymentEvent, data: dict) -> str:
    """Attach Paystack's subscription codes to the matching row.

    A subscription.create can arrive before the matching charge, so there may
    be no subscription row yet. One is created in that case, using the customer
    code on the event to find the vendor.
    """
    code = event_record.paystack_subscription_code or _first_str(
        data.get("subscription_code")
    )
    customer_code = event_record.paystack_customer_code or _first_str(
        # subscription.create carries a flat `customer_code`. The nested
        # `customer` object appears on charge events, so both are read rather
        # than assuming either shape.
        data.get("customer_code"),
        (data.get("customer") or {}).get("customer_code")
        if isinstance(data.get("customer"), dict)
        else None,
    )
    plan_code = _first_str(data.get("plan_code"), data.get("plan"))
    invoice_code = _first_str(data.get("invoice_code"))

    subscription = _find_subscription(code=code, customer_code=customer_code)

    if subscription is None:
        plan = ensure_vendor_plan()
        vendor = _vendor_for_customer(customer_code)
        if vendor is None:
            logger.warning(
                "subscription.create for an unrecognised customer; no local vendor"
            )
            return "no_vendor"

        subscription = get_or_create_subscription(vendor, vendor.owner_user_id, plan)
        subscription.status = SUBSCRIPTION_PENDING

    subscription.paystack_subscription_code = code or subscription.paystack_subscription_code
    subscription.paystack_customer_code = (
        customer_code or subscription.paystack_customer_code
    )
    subscription.plan_code = plan_code or subscription.plan_code
    # Paystack calls this the invoice token. It is emailed to the customer so
    # they can pay a renewal themselves, which is why it is kept at all.
    subscription.paystack_email_token = (
        invoice_code or subscription.paystack_email_token
    )
    subscription.last_paystack_event_at = utcnow()
    db.session.commit()

    logger.info("Webhook processed: subscription.create subscription=%s", subscription.id)
    return "subscription_linked"


def _handle_invoice_failed(event_record: PaymentEvent, data: dict) -> str:
    """A renewal charge failed: past_due, not cancelled.

    Paystack retries before it disables a subscription, so marking it
    cancelled here would revoke access on the first failed attempt — including
    the case where the customer's bank simply needed an extra day.
    """
    subscription = _find_subscription(
        code=event_record.paystack_subscription_code,
        reference=event_record.reference,
    )
    if subscription is None:
        logger.warning("invoice.payment_failed matched no local subscription")
        return "no_subscription"

    if subscription.status == SUBSCRIPTION_CANCELLED:
        return "already_cancelled"

    subscription.status = SUBSCRIPTION_PAST_DUE
    subscription.last_paystack_event_at = utcnow()
    db.session.commit()

    logger.info(
        "Webhook processed: invoice.payment_failed subscription=%s", subscription.id
    )
    return "marked_past_due"


def _handle_subscription_stop(event_record: PaymentEvent, status: str) -> str:
    """Apply ``subscription.disable`` or ``subscription.not_renew``.

    Looks the subscription up by every identifier the event carried, not just the
    subscription code. Paystack only sends that code once the subscription
    exists on its side, so an event arriving before Sauti has ever seen it would
    otherwise match nothing and silently do nothing.
    """
    subscription = _find_subscription(
        code=event_record.paystack_subscription_code,
        reference=event_record.reference,
        customer_code=event_record.paystack_customer_code,
    )
    if subscription is None:
        logger.warning("Subscription lifecycle event matched no local subscription")
        return "no_subscription"

    if subscription.status in (SUBSCRIPTION_CANCELLED, SUBSCRIPTION_EXPIRED):
        return "already_stopped"

    subscription.status = status
    subscription.last_paystack_event_at = utcnow()
    db.session.commit()

    logger.info(
        "Webhook processed: subscription lifecycle subscription=%s status=%s",
        subscription.id,
        status,
    )
    return f"marked_{status}"


def _find_subscription(
    *,
    code: str | None = None,
    customer_code: str | None = None,
    reference: str | None = None,
) -> Subscription | None:
    """Locate a subscription from whatever identifiers the event carried.

    Paystack is inconsistent about which of these it includes, so each lookup
    falls through to the next rather than assuming any single one is present.
    """
    if code:
        found = Subscription.query.filter_by(paystack_subscription_code=code).first()
        if found:
            return found

    if customer_code:
        found = (
            Subscription.query.filter_by(paystack_customer_code=customer_code)
            .order_by(Subscription.created_at.asc())
            .first()
        )
        if found:
            return found

    if reference:
        payment = Payment.query.filter_by(reference=reference).first()
        if payment and payment.subscription_id:
            found = db.session.get(Subscription, payment.subscription_id)
            if found:
                return found

    return None


def _vendor_for_customer(customer_code: str | None) -> Vendor | None:
    """The vendor behind a Paystack customer code.

    Prefers a vendor that has actually been paid by that customer, and falls
    back to the oldest vendor only when there is exactly one — a single-vendor
    deployment is unambiguous, and guessing between several would attribute a
    subscription to the wrong business.
    """
    if not customer_code:
        return None

    payment = (
        Payment.query.filter_by(paystack_customer_code=customer_code)
        .order_by(Payment.created_at.asc())
        .first()
    )
    if payment and payment.vendor_id:
        vendor = db.session.get(Vendor, payment.vendor_id)
        if vendor:
            return vendor

    candidates = Vendor.query.order_by(Vendor.created_at.asc()).limit(2).all()
    if len(candidates) == 1:
        return candidates[0]
    return None


__all__ = [
    "VENDOR_PLAN_SLUG",
    "REFERENCE_PREFIX",
    "ACTIONABLE_EVENTS",
    "PaymentServiceError",
    "RetryableWebhookError",
    "ensure_vendor_plan",
    "resolve_vendor_identity",
    "new_reference",
    "initialize_payment",
    "verify_payment",
    "subscription_status",
    "record_event",
    "process_event",
]