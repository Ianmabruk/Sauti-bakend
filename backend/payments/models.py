"""Paystack subscription models.

Four additive tables: ``plans``, ``subscriptions``, ``payments`` and
``payment_events``. Nothing here modifies or replaces an existing Sauti table.

Money is stored the way the rest of this codebase already stores money: an
integer count of the currency's smallest unit in ``amount_minor``, alongside a
separate ``currency`` string. ``KES`` has two decimal places, so the vendor
subscription of KSh 100 is ``10000`` in ``amount_minor``. The multiplication is
defined once in :func:`major_to_minor` and never repeated inline, because a
hand-written ``100 * 100`` in three places is how an amount silently goes off by
a factor of 100.

Paystack has no zero-decimal currencies, so ``minor_exponent`` is a column
rather than a constant: if the product ever sells in a currency that charges
per whole unit, the row carries the exponent and nothing else changes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..db import db
from ..models import TimestampMixin, generate_uuid

#: Currency exponent table. Paystack sends and expects amounts in the smallest
#: unit of the currency. Only currencies Sauti can actually sell in appear here;
#: an unknown currency is a configuration error, not a guess.
CURRENCY_EXPONENTS: dict[str, int] = {
    "KES": 2,
    "NGN": 2,
    "GHS": 2,
    "ZAR": 2,
    "USD": 2,
    "EUR": 2,
}

#: The one currency the vendor subscription is priced in.
SUBSCRIPTION_CURRENCY = "KES"


def utcnow() -> datetime:
    """Timezone-aware current time.

    The models in this codebase default their timestamps with
    ``datetime.now(timezone.utc)``; this is the same clock exposed as a function
    so that expiry maths in the service layer reads clearly.
    """
    return datetime.now(timezone.utc)


def as_aware(value: datetime | None) -> datetime | None:
    """Coerce a stored timestamp to timezone-aware UTC.

    Necessary, not defensive. A column declared ``DateTime(timezone=True)`` comes
    back aware from PostgreSQL but *naive* from SQLite, because SQLite has no
    timezone type and silently drops the offset. Comparing the two raises
    ``TypeError: can't compare offset-naive and offset-aware datetimes``.

    That failure is the worst kind to ship: it never appears in production on
    Postgres, and it breaks every expiry check in local development and in the
    test suite. Treating a naive value as UTC is correct in both, because every
    timestamp this module writes is UTC.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def major_to_minor(amount: float | int, currency: str = SUBSCRIPTION_CURRENCY) -> int:
    """Convert a major-unit amount (KSh 100) to the smallest unit (10000).

    Rounds half-up on the integer result rather than truncating, so a price like
    10.005 becomes 1001 instead of 1000. Rejecting an unknown currency is
    deliberate: silently assuming an exponent would let a misconfigured plan
    charge the wrong amount by orders of magnitude.
    """
    exponent = CURRENCY_EXPONENTS.get((currency or "").upper())
    if exponent is None:
        raise ValueError(f"Unsupported currency: {currency!r}")

    # Decimal arithmetic, not float. 100.10 * 100 is 10009.999999999998 in
    # binary floating point, and that error would end up in a payment amount.
    from decimal import Decimal, ROUND_HALF_UP

    scaled = Decimal(str(amount)) * (Decimal(10) ** exponent)
    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def minor_to_major(amount_minor: int, currency: str = SUBSCRIPTION_CURRENCY) -> float:
    """Inverse of :func:`major_to_minor`, for display only.

    Used for the "KSh 100 / month" label on the pricing screen. It goes through
    ``Decimal`` for the same reason and returns a float because JSON consumers
    expect a JSON number.
    """
    from decimal import Decimal

    exponent = CURRENCY_EXPONENTS.get((currency or "").upper())
    if exponent is None:
        raise ValueError(f"Unsupported currency: {currency!r}")

    return float(Decimal(int(amount_minor)) / (Decimal(10) ** exponent))


# ---------------------------------------------------------------------------
# Status vocabularies
#
# These are the exact strings the API contract promises. They are module
# constants rather than bare literals so a typo is an AttributeError at import
# time instead of a subscription stuck in a status no code path ever matches.
# ---------------------------------------------------------------------------

SUBSCRIPTION_INACTIVE = "inactive"
SUBSCRIPTION_PENDING = "pending"
SUBSCRIPTION_ACTIVE = "active"
SUBSCRIPTION_PAST_DUE = "past_due"
SUBSCRIPTION_CANCELLED = "cancelled"
SUBSCRIPTION_EXPIRED = "expired"

SUBSCRIPTION_STATUSES = (
    SUBSCRIPTION_INACTIVE,
    SUBSCRIPTION_PENDING,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_PAST_DUE,
    SUBSCRIPTION_CANCELLED,
    SUBSCRIPTION_EXPIRED,
)

#: Statuses that entitle a vendor to the paid features. Kept as its own tuple so
#: the frontend-facing boolean and any future server-side feature gate agree.
ENTITLED_SUBSCRIPTION_STATUSES = (
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_PAST_DUE,
)

PAYMENT_PENDING = "pending"
PAYMENT_SUCCESS = "success"
PAYMENT_FAILED = "failed"
PAYMENT_ABANDONED = "abandoned"

PAYMENT_STATUSES = (
    PAYMENT_PENDING,
    PAYMENT_SUCCESS,
    PAYMENT_FAILED,
    PAYMENT_ABANDONED,
)


class Plan(TimestampMixin, db.Model):
    """A sellable plan. Populated from configuration or the Paystack Test
    dashboard, never created implicitly by a checkout request."""

    __tablename__ = "plans"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    slug = db.Column(db.String(80), nullable=False)
    name = db.Column(db.String(160), nullable=False)

    #: Price in the smallest currency unit. 10000 == KSh 100.
    amount_minor = db.Column(db.Integer, nullable=False)
    #: ``server_default`` is declared on every column that has a migration-level
    #: default, so the schema produced by ``create_all`` in development is
    #: byte-identical to the one migration 0005 produces in production. Without
    #: this the two drift: a column created in prod has a default the model does
    #: not know about, so an insert that omits it succeeds against Postgres and
    #: fails against SQLite, or Alembic later emits a pointless ALTER to drop a
    #: default the application relies on.
    currency = db.Column(
        db.String(8),
        default=SUBSCRIPTION_CURRENCY,
        server_default=SUBSCRIPTION_CURRENCY,
        nullable=False,
    )
    #: How often the subscription renews: "monthly" or "yearly".
    interval = db.Column(
        db.String(20), default="monthly", server_default="monthly", nullable=False
    )
    #: Decimal places in the currency. See :func:`major_to_minor`.
    minor_exponent = db.Column(
        db.Integer, default=2, server_default="2", nullable=False
    )

    #: The real Paystack plan code (PLN_...). Nullable on purpose: a plan can
    #: exist locally before anyone opens the Test dashboard and creates it, and
    #: the payments service refuses to check out rather than inventing a code.
    paystack_plan_code = db.Column(db.String(80), nullable=True)

    description = db.Column(db.String(500), nullable=True)
    is_active = db.Column(
        db.Boolean, default=True, server_default=db.true(), nullable=False
    )

    __table_args__ = (
        # Named, and matching migration 0005. An unnamed ``unique=True`` emits a
        # constraint Postgres auto-names ``plans_slug_key``, so the model and the
        # migration would disagree on the name of the same constraint.
        db.UniqueConstraint("slug", name="uq_plans_slug"),
        db.Index("idx_plans_active", "is_active"),
        db.Index("idx_plans_paystack_plan", "paystack_plan_code"),
    )

    @property
    def amount_major(self) -> float:
        """The price in whole currency units, for display."""
        return minor_to_major(self.amount_minor, self.currency)

    @property
    def has_paystack_plan(self) -> bool:
        """Whether this plan can actually be charged for right now."""
        return bool(self.paystack_plan_code)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "amountMinor": self.amount_minor,
            "amount": self.amount_major,
            "currency": self.currency,
            "interval": self.interval,
            "description": self.description,
            "isActive": self.is_active,
            "available": self.has_paystack_plan,
        }


class Subscription(TimestampMixin, db.Model):
    """A vendor's subscription to a plan.

    One row per (vendor_id, plan_id). ``paystack_subscription_code`` and
    ``paystack_customer_code`` are nullable because the row is created the
    moment a vendor starts checkout, before Paystack has issued anything. That
    is intentional: an in-flight subscription is the state the frontend needs to
    render, and creating it lazily on the first webhook would leave a gap where
    a vendor has paid nothing and yet no row exists.
    """

    __tablename__ = "subscriptions"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)

    vendor_id = db.Column(
        db.String,
        db.ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(
        db.String,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    plan_id = db.Column(
        db.String,
        db.ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
    )

    paystack_subscription_code = db.Column(db.String(120), nullable=True)
    paystack_customer_code = db.Column(db.String(120), nullable=True)
    #: Paystack issues this when the customer pays; used to email them a
    #: payment link for automatic charges.
    paystack_email_token = db.Column(db.String(120), nullable=True)
    plan_code = db.Column(db.String(80), nullable=True)

    status = db.Column(
        db.String(20),
        default=SUBSCRIPTION_INACTIVE,
        server_default=SUBSCRIPTION_INACTIVE,
        nullable=False,
    )
    start_date = db.Column(db.DateTime(timezone=True), nullable=True)
    next_payment_date = db.Column(db.DateTime(timezone=True), nullable=True)
    #: Last Paystack-sourced fact we acted on. Lets a retried webhook that
    #: carries an older status be recognised as stale and ignored.
    last_paystack_event_at = db.Column(db.DateTime(timezone=True), nullable=True)

    vendor = db.relationship("Vendor", foreign_keys=[vendor_id])
    user = db.relationship("User", foreign_keys=[user_id])
    plan = db.relationship("Plan", foreign_keys=[plan_id])

    __table_args__ = (
        db.UniqueConstraint(
            "vendor_id", "plan_id", name="uq_subscriptions_vendor_plan"
        ),
        # Named explicitly rather than ``index=True`` on the column. ``index=True``
        # generates ``ix_subscriptions_status`` while migration 0005 creates
        # ``idx_subscriptions_status``, so a database that has run the migration
        # and then had the table created or altered from the model ends up with two
        # indexes on the same column under two names.
        db.Index("idx_subscriptions_status", "status"),
        db.Index("idx_subscriptions_user", "user_id"),
        db.Index("idx_subscriptions_paystack_sub", "paystack_subscription_code"),
        # _find_subscription falls back to the customer code when a webhook omits
        # the subscription code, which Paystack does routinely for renewal charges.
        # That lookup runs on every renewal, so it is indexed like any other hot
        # path rather than left as a sequential scan of the whole table.
        db.Index("idx_subscriptions_customer_code", "paystack_customer_code"),
    )

    # -- derived state ----------------------------------------------------

    @property
    def is_entitled(self) -> bool:
        """Whether the paid features should be available.

        ``past_due`` still counts as entitled. Paystack retries a failed renewal
        before it gives up, and revoking access the moment a retry is scheduled
        would lock out vendors whose bank simply took an extra day — a far worse
        failure than a few days of grace. A genuine termination arrives as
        ``subscription.disable``, which is not in this tuple.
        """
        return self.status in ENTITLED_SUBSCRIPTION_STATUSES

    def mark_active(self, *, now: datetime | None = None) -> bool:
        """Move to ``active`` and start or extend the paid period.

        Returns True when the paid period actually moved forward, False when the
        subscription was already active for this same window. The caller uses
        that to decide whether to count a renewal, which is what stops a
        replayed ``charge.success`` from granting a second month.
        """
        moment = now or utcnow()

        window_end = as_aware(self.next_payment_date)

        if self.status == SUBSCRIPTION_ACTIVE and window_end:
            if window_end > moment:
                # Already paid for a window that has not run out. Nothing to do;
                # extending now would be a free extra month on every retry.
                return False
            # The previous window has elapsed, so this charge renews it.
            self.next_payment_date = moment + self.period_from(moment)
            self.updated_at = moment
            return True

        self.status = SUBSCRIPTION_ACTIVE
        self.start_date = self.start_date or moment
        self.next_payment_date = moment + self.period_from(moment)
        self.updated_at = moment
        return True

    def period_from(self, moment: datetime) -> timedelta:
        """The length of one billing period for the linked plan."""
        interval = (self.plan.interval if self.plan else "monthly") or "monthly"
        if interval == "yearly":
            return timedelta(days=365)
        if interval == "weekly":
            return timedelta(weeks=1)
        if interval == "daily":
            return timedelta(days=1)
        return timedelta(days=30)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "vendorId": self.vendor_id,
            "userId": self.user_id,
            "planId": self.plan_id,
            "planCode": self.plan_code,
            "status": self.status,
            "isEntitled": self.is_entitled,
            "startDate": _iso(self.start_date),
            "nextPaymentDate": _iso(self.next_payment_date),
            "createdAt": _iso(self.created_at),
            "updatedAt": _iso(self.updated_at),
            "plan": self.plan.to_dict() if self.plan else None,
        }


class Payment(TimestampMixin, db.Model):
    """One checkout attempt against Paystack.

    A row is written *before* Paystack is called, so the reference the backend
    generates is durable. If the process dies mid-request the abandoned row is
    what the next reuse-window sweep finds, rather than a reference that exists
    at Paystack but nowhere in Sauti.

    This is a separate table from ``transactions``. That one is the M-Pesa
    order-payment record and is keyed on ``orders``; subscriptions are not
    orders, they are recurring billing against a vendor. Reusing it would have
    meant a nullable ``orders`` foreign key and a second meaning per row.
    """

    __tablename__ = "payments"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)

    vendor_id = db.Column(
        db.String,
        db.ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True
    )
    user_id = db.Column(
        db.String,
        db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    plan_id = db.Column(
        db.String,
        db.ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
    )
    subscription_id = db.Column(
        db.String,
        db.ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True,
    )

    #: The reference Sauti generated and sent to Paystack. Unique, so a
    #: collision with a real transaction cannot silently overwrite a payment.
    #:
    #: The unique constraint is left to do the indexing. Declaring ``index=True``
    #: as well asks for a second, plain index over a column the unique constraint
    #: already indexes — two structures, double the write cost on every insert and
    #: no additional lookup served, since the unique index answers equality
    #: queries perfectly well on its own.
    reference = db.Column(db.String(120), nullable=False)
    authorization_code = db.Column(db.String(120), nullable=True)
    access_code = db.Column(db.String(120), nullable=True)
    #: Where Paystack wants the customer sent to approve this payment.
    #:
    #: Stored because Paystack returns it once, at initialization, and not again
    #: on any later read. Without it a customer who abandons the popup and comes
    #: back cannot be resumed: a fresh initialization would mint a second
    #: reference and a second checkout session for a payment they already
    #: started. It is a Paystack-hosted URL, so it carries no secret, but it is
    #: still per-customer and therefore not logged.
    authorization_url = db.Column(db.Text, nullable=True)

    amount_minor = db.Column(db.Integer, nullable=False)
    currency = db.Column(
        db.String(8),
        default=SUBSCRIPTION_CURRENCY,
        server_default=SUBSCRIPTION_CURRENCY,
        nullable=False,
    )

    status = db.Column(
        db.String(20),
        default=PAYMENT_PENDING,
        server_default=PAYMENT_PENDING,
        nullable=False,
    )
    #: How the money moved, e.g. "card", "mobilepush", "bank". Set from Paystack
    #: after verification; never from the browser.
    channel = db.Column(db.String(40), nullable=True)
    #: Paystack's own identifier for the customer, used to associate future
    #: recurring charges with this vendor.
    paystack_customer_code = db.Column(db.String(120), nullable=True)

    #: Truncated Paystack message, e.g. a decline reason. Kept short on purpose:
    #: provider responses can echo request content, and this table is long-lived.
    failure_reason = db.Column(db.String(255), nullable=True)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=True)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=True)

    vendor = db.relationship("Vendor", foreign_keys=[vendor_id])
    user = db.relationship("User", foreign_keys=[user_id])
    plan = db.relationship("Plan", foreign_keys=[plan_id])
    subscription = db.relationship("Subscription", foreign_keys=[subscription_id])

    __table_args__ = (
        db.UniqueConstraint("reference", name="uq_payments_reference"),
        db.Index("idx_payments_status", "status"),
        db.Index("idx_payments_vendor_status", "vendor_id", "status"),
        db.Index("idx_payments_user_status", "user_id", "status"),
        # Renewal matching reads this on every charge that arrives without a
        # subscription code.
        db.Index("idx_payments_customer_code", "paystack_customer_code"),
    )

    @property
    def is_settled(self) -> bool:
        """Whether Paystack has confirmed this money moved."""
        return self.status == PAYMENT_SUCCESS

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "reference": self.reference,
            "amountMinor": self.amount_minor,
            "amount": minor_to_major(self.amount_minor, self.currency),
            "currency": self.currency,
            "status": self.status,
            "channel": self.channel,
            "vendorId": self.vendor_id,
            "planId": self.plan_id,
            "paidAt": _iso(self.paid_at),
            "createdAt": _iso(self.created_at),
        }


class PaymentEvent(db.Model):
    """Every webhook delivery Paystack sent, processed or not.

    This table is the replay defence. The unique constraint on
    ``(provider, event_key)`` is what makes duplicate suppression a database
    guarantee rather than a check-then-insert race: two concurrent deliveries of
    the same event both try to insert, and exactly one wins. The loser catches
    the IntegrityError and returns 200, so Paystack stops retrying and nothing is
    applied twice.

    Rows are never deleted by application code. A webhook that is recorded but
    not handled — an unknown event, or one that arrived while a feature was off
    — is kept so the fact it happened survives the process that ignored it.
    """

    __tablename__ = "payment_events"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)

    provider = db.Column(
        db.String(40), default="paystack", server_default="paystack", nullable=False
    )
    event_type = db.Column(db.String(80), nullable=False)

    #: Stable identity of the delivery. Paystack does not send a delivery id, so
    #: this is derived from the event body by :func:`derive_event_key`.
    event_key = db.Column(db.String(255), nullable=False)

    #: The payment reference the event concerns, when it names one. Indexed
    #: because that is how a ``charge.success`` finds its way back to a Payment.
    reference = db.Column(db.String(120), nullable=True)
    paystack_subscription_code = db.Column(db.String(120), nullable=True)
    paystack_customer_code = db.Column(db.String(120), nullable=True)

    #: Only the fields needed to process the event. Deliberately not the whole
    #: payload: a webhook body can carry customer details that have no business
    #: being retained, and retaining them creates a data-protection obligation
    #: this table has no way to discharge.
    payload = db.Column(db.JSON, nullable=True)

    processed = db.Column(
        db.Boolean, default=False, server_default=db.false(), nullable=False
    )
    processing_error = db.Column(db.String(255), nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=utcnow,
        server_default=db.func.now(),
        nullable=False,
    )
    processed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            "provider", "event_key", name="uq_payment_events_provider_key"
        ),
        db.Index("idx_payment_events_type", "event_type"),
        db.Index("idx_payment_events_processed", "processed"),
        db.Index("idx_payment_events_reference", "reference"),
        # Renewal matching reads this on every charge that arrives without a
        # subscription code.
        db.Index("idx_payment_events_customer_code", "paystack_customer_code"),
        db.Index("idx_payment_events_created", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "eventType": self.event_type,
            "eventKey": self.event_key,
            "reference": self.reference,
            "processed": self.processed,
            "createdAt": _iso(self.created_at),
            "processedAt": _iso(self.processed_at),
        }


def derive_event_key(event: dict) -> str:
    """A stable identity for one webhook delivery.

    Paystack sends no delivery id, so the key is built from the parts that make
    the event unique: its type and the transaction, invoice or subscription it
    refers to. Two genuinely separate events of the same type about the same
    object therefore collapse into one — which is the correct behaviour, because
    acting on either produces the same state.

    When nothing identifying is present, the raw body is hashed so the event is
    still recorded. That path cannot deduplicate identical anonymous events, but
    it keeps every delivery accounted for instead of dropping unknown shapes.
    """
    event_type = str(event.get("event") or "unknown")

    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    transaction = _nested(data, "transaction")
    subscription = _nested(data, "subscription")
    invoice = _nested(data, "invoice")
    customer = _nested(data, "customer")

    reference = _first_str(
        data.get("reference"),
        transaction.get("reference"),
    )
    subscription_code = _first_str(
        subscription.get("subscription_code"),
        subscription.get("code"),
        data.get("subscription_code"),
    )
    invoice_code = _first_str(
        invoice.get("invoice_code"),
        data.get("invoice_code"),
    )
    customer_code = _first_str(customer.get("customer_code"))
    transaction_id = _first_str(data.get("id"), transaction.get("id"))

    parts = [event_type]
    for candidate in (
        reference,
        subscription_code,
        invoice_code,
        transaction_id,
        customer_code,
    ):
        if candidate:
            parts.append(candidate)
            break

    if len(parts) > 1:
        return ":".join(parts)

    import hashlib

    import json

    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
    return f"{event_type}:body:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _nested(data: dict, key: str) -> dict:
    """``data[key]`` when it is a dict, otherwise an empty dict.

    Paystack nests differently per event: some send a full object under
    ``transaction``, some send ``subscription_code`` as a flat string, some send
    a bare integer id. Normalising to a dict here means the caller can ask for
    any field without repeating the isinstance dance on every one.
    """
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _first_str(*candidates) -> str | None:
    """The first candidate that is a non-empty string."""
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _iso(moment: datetime | None) -> str | None:
    """Render a timestamp as an ISO-8601 string, or None."""
    return moment.isoformat() if moment else None


__all__ = [
    "CURRENCY_EXPONENTS",
    "SUBSCRIPTION_CURRENCY",
    "SUBSCRIPTION_STATUSES",
    "SUBSCRIPTION_INACTIVE",
    "SUBSCRIPTION_PENDING",
    "SUBSCRIPTION_ACTIVE",
    "SUBSCRIPTION_PAST_DUE",
    "SUBSCRIPTION_CANCELLED",
    "SUBSCRIPTION_EXPIRED",
    "ENTITLED_SUBSCRIPTION_STATUSES",
    "PAYMENT_STATUSES",
    "PAYMENT_PENDING",
    "PAYMENT_SUCCESS",
    "PAYMENT_FAILED",
    "PAYMENT_ABANDONED",
    "Plan",
    "Subscription",
    "Payment",
    "PaymentEvent",
    "major_to_minor",
    "minor_to_major",
    "derive_event_key",
    "utcnow",
    "as_aware",
]
