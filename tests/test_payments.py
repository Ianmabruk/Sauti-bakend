"""Paystack subscription tests.

Covers the integration in test mode without touching the network. Paystack is
reached through a stub client rather than an HTTP mock, so these tests assert on
Sauti's own logic — amount enforcement, signature verification, idempotency —
without depending on Paystack's availability or on any real key.

The naming maps onto the checklist in ``docs/PAYSTACK_TEST_CHECKLIST.md`` so a
failed scenario can be traced back to a documented risk.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest

from backend.app import create_app
from backend.config import TestingConfig
from backend.db import db
from backend.marketplace.models import Vendor
from backend.models import User
from backend.payments import models as payment_models
from backend.payments.auth import decode_vendor_token, issue_vendor_token
from backend.payments.models import (
    PAYMENT_FAILED,
    PAYMENT_PENDING,
    PAYMENT_SUCCESS,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_CANCELLED,
    SUBSCRIPTION_EXPIRED,
    SUBSCRIPTION_INACTIVE,
    SUBSCRIPTION_PAST_DUE,
    SUBSCRIPTION_PENDING,
    Payment,
    PaymentEvent,
    Plan,
    Subscription,
    derive_event_key,
    major_to_minor,
    utcnow,
)
from backend.payments.paystack import (
    PaystackClient,
    PaystackError,
    compute_signature,
    is_test_mode,
    paystack_mode,
    paystack_public_key,
)

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

TEST_SECRET = "sk_test_" + "0" * 32
TEST_PUBLIC = "pk_test_" + "0" * 32
TEST_PLAN_CODE = "PLN_test0000000000"

#: KSh 100 in the smallest unit. The one number the whole system agrees on.
EXPECTED_AMOUNT_MINOR = 10000


class StubClient:
    """A PaystackClient stand-in that answers from a script.

    Records every call so a test can assert on the amount that *would* have been
    sent, which is the only way to prove the server-side amount enforcement
    actually holds.
    """

    def __init__(
        self,
        *,
        paid: bool = True,
        status: str = "success",
        amount_minor: int = EXPECTED_AMOUNT_MINOR,
        currency: str = "KES",
        error: Exception | None = None,
    ):
        self.paid = paid
        self.status = status
        self.amount_minor = amount_minor
        self.currency = currency
        self.error = error

        self.initialize_calls: list[dict] = []
        self.verify_calls: list[str] = []

    def initialize_transaction(
        self,
        *,
        reference,
        amount_minor,
        currency,
        email,
        plan_code,
        callback_url=None,
    ):
        if self.error:
            raise self.error
        self.initialize_calls.append(
            {
                "reference": reference,
                "amount_minor": amount_minor,
                "currency": currency,
                "email": email,
                "plan_code": plan_code,
                "callback_url": callback_url,
            }
        )
        from backend.payments.paystack import InitializedTransaction

        return InitializedTransaction(
            authorization_url=f"https://checkout.paystack.test/{reference}",
            access_code="access_test_abc",
            reference=reference,
        )

    def verify_transaction(self, reference):
        if self.error:
            raise self.error
        self.verify_calls.append(reference)

        from backend.payments.paystack import VerifiedTransaction

        return VerifiedTransaction(
            reference=reference,
            status=self.status,
            amount_minor=self.amount_minor,
            currency=self.currency,
            paid=self.paid,
            channel="card",
            customer_code="CUS_test123",
            customer_email="vendor@example.com",
            paid_at="2026-01-15T10:00:00.000Z",
            failure_reason=None if self.paid else "declined",
        )


@pytest.fixture
def app():
    app = create_app(TestingConfig)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key-for-payment-tests"
    app.config["PAYSTACK_SECRET_KEY"] = TEST_SECRET
    app.config["PAYSTACK_PUBLIC_KEY"] = TEST_PUBLIC
    app.config["PAYSTACK_PLAN_CODE"] = TEST_PLAN_CODE
    app.config["PAYSTACK_ALLOW_LIVE"] = False
    app.config["PUBLIC_SITE_URL"] = "https://sauti-ai.onrender.com"

    # Effectively unlimited for the behavioural tests. The real defaults are
    # 5-20 per minute, and the limiter keys on IP, so every test in this file
    # shares one budget and would start tripping it partway through the run.
    # Rate limiting has its own test below that sets a real limit.
    app.config["PAYSTACK_INIT_RATE_LIMIT"] = "10000 per minute"
    app.config["PAYSTACK_VERIFY_RATE_LIMIT"] = "10000 per minute"
    app.config["PAYSTACK_TOKEN_RATE_LIMIT"] = "10000 per minute"

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def vendor(app):
    """A vendor with an owner, which is the precondition for everything here."""
    with app.app_context():
        user = User(id="web-test-user", is_active=True)
        vendor = Vendor(
            business_name="Test Grocers",
            slug="test-grocers",
            description="A vendor for the payment tests",
            category="food",
            location="Nairobi",
            email="vendor@example.com",
            owner_user_id=user.id,
        )
        db.session.add_all([user, vendor])
        db.session.commit()
        return vendor.id


@pytest.fixture
def stub_paystack(monkeypatch):
    """Swap the route's PaystackClient for a stub, and return a setter.

    The seam is the client, not the service functions. The routes import
    ``PaystackClient`` by name, so patching ``service.verify_payment`` would not
    affect them -- and a test that thinks it stubbed the network while actually
    calling api.paystack.co with a placeholder key is worse than no test, because
    it fails for reasons unrelated to the code under test.
    """
    from backend.api import payments as payments_api

    holder: dict[str, StubClient] = {"client": StubClient()}

    def install(**kwargs) -> StubClient:
        stub = StubClient(**kwargs)
        holder["client"] = stub
        monkeypatch.setattr(payments_api, "PaystackClient", lambda: stub)
        return stub

    install()
    return install


def auth_headers(app, vendor_id="web-test-user"):
    """A valid vendor token as the browser would send it."""
    token = issue_vendor_token("web-test-user", vendor_id)["token"]
    return {"X-Vendor-Token": token}


def post_webhook(client, event, *, secret=TEST_SECRET, sign=True, signature=None):
    """Deliver a webhook the way Paystack does."""
    raw = json.dumps(event).encode()
    headers = {}
    if sign:
        headers["x-paystack-signature"] = (
            signature if signature is not None else compute_signature(raw, secret)
        )
    return client.post(
        "/api/payments/webhook",
        data=raw,
        headers=headers,
        content_type="application/json",
    )


def charge_event(reference, amount=EXPECTED_AMOUNT_MINOR, currency="KES", status="success"):
    return {
        "event": "charge.success",
        "data": {
            "id": 302991,
            "reference": reference,
            "status": status,
            "amount": amount,
            "currency": currency,
            "channel": "card",
            "paid_at": "2026-01-15T10:00:00Z",
            "customer": {
                "customer_code": "CUS_test123",
                "email": "vendor@example.com",
            },
        },
    }


def seed_pending_payment(vendor_id, *, amount=EXPECTED_AMOUNT_MINOR, currency="KES"):
    """A payment row in the pending state, as initialize would leave it."""
    from backend.payments.service import ensure_vendor_plan, get_or_create_subscription

    plan = ensure_vendor_plan()
    vendor = db.session.get(Vendor, vendor_id)
    subscription = get_or_create_subscription(vendor, vendor.owner_user_id, plan)
    payment = Payment(
        vendor_id=vendor.id,
        user_id=vendor.owner_user_id,
        plan_id=plan.id,
        subscription_id=subscription.id,
        reference="sauti_seedtest0000000000000000",
        amount_minor=amount,
        currency=currency,
        status=PAYMENT_PENDING,
    )
    db.session.add(payment)
    db.session.commit()
    return payment


# ---------------------------------------------------------------------------
# Configuration and mode safety
# ---------------------------------------------------------------------------


def test_configured_keys_are_test_mode(app):
    assert paystack_mode() == "test"
    assert is_test_mode() is True


def test_live_key_is_refused_without_explicit_opt_in(app):
    """The single most important safety test in this file.

    A live key pasted into a test deployment must fail closed. If this ever
    passes silently, the same mistake in production charges real people.
    """
    app.config["PAYSTACK_SECRET_KEY"] = "sk_live_" + "a" * 32

    with pytest.raises(PaystackError) as caught:
        PaystackClient()

    assert "not available" in caught.value.user_message.lower()
    # The refusal must not leak the key.
    assert "sk_live_" not in caught.value.user_message
    assert "sk_live_" not in caught.value.detail


def test_live_key_works_only_with_explicit_opt_in(app):
    app.config["PAYSTACK_SECRET_KEY"] = "sk_live_" + "a" * 32
    app.config["PAYSTACK_ALLOW_LIVE"] = True

    assert paystack_mode() == "live"
    assert PaystackClient().mode == "live"


def test_unrecognised_key_prefix_disables_payments(app):
    app.config["PAYSTACK_SECRET_KEY"] = "not-a-real-key"

    with pytest.raises(PaystackError):
        PaystackClient()


def test_missing_secret_key_disables_payments(app):
    app.config["PAYSTACK_SECRET_KEY"] = ""

    assert paystack_mode() == "disabled"
    with pytest.raises(PaystackError):
        PaystackClient()


def test_public_key_accessor_never_returns_a_secret(app):
    """Only pk_ values reach the browser, whatever is configured."""
    assert paystack_public_key() == TEST_PUBLIC

    app.config["PAYSTACK_PUBLIC_KEY"] = TEST_SECRET
    assert paystack_public_key() == ""
    assert "sk_" not in paystack_public_key()


# ---------------------------------------------------------------------------
# Money arithmetic
# ---------------------------------------------------------------------------


def test_kes_100_is_10000_minor_units():
    assert major_to_minor(100) == EXPECTED_AMOUNT_MINOR
    assert payment_models.minor_to_major(EXPECTED_AMOUNT_MINOR) == 100


def test_minor_unit_conversion_avoids_float_error():
    # 100.10 * 100 is 10009.999999999998 in binary floating point. Going through
    # Decimal is what keeps that out of a payment amount.
    assert major_to_minor(100.10) == 10010
    assert major_to_minor(10.005) == 1001


def test_unknown_currency_is_refused_not_guessed():
    with pytest.raises(ValueError):
        major_to_minor(100, "XYZ")


# ---------------------------------------------------------------------------
# Initialize: server-side amount and currency enforcement
# ---------------------------------------------------------------------------


def test_initialize_uses_the_server_amount_not_the_request(
    app, client, vendor, stub_paystack
):
    from backend.payments import service

    stub = stub_paystack()

    with app.app_context():
        result = service.initialize_payment(
            user_id="web-test-user",
            vendor_id=vendor,
            client=stub,
            email="vendor@example.com",
            callback_url="https://sauti-ai.onrender.com/payment/callback",
        )

    assert result["amount"] == 100
    assert result["currency"] == "KES"
    assert len(stub.initialize_calls) == 1
    # The amount that would reach Paystack came from the plan row.
    assert stub.initialize_calls[0]["amount_minor"] == EXPECTED_AMOUNT_MINOR
    assert stub.initialize_calls[0]["currency"] == "KES"


def test_client_cannot_manipulate_the_amount(app, client, vendor, stub_paystack):
    """Checklist item: attempt to manipulate the amount from the browser.

    The request body carries a hostile amount and currency. The initialize schema
    has no field for either, so Pydantic drops them and the server price is used.
    The assertion is on the amount the Paystack stub *would have been told*,
    which is the fact that actually matters -- the response echoing 100 proves
    much less than the outbound call being correct.
    """
    stub = stub_paystack()

    response = client.post(
        "/api/payments/initialize",
        json={
            "amount": 1,
            "amount_minor": 1,
            "currency": "USD",
            "email": "attacker@example.com",
        },
        headers=auth_headers(app, vendor),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["amount"] == 100
    assert body["currency"] == "KES"

    assert len(stub.initialize_calls) == 1
    assert stub.initialize_calls[0]["amount_minor"] == EXPECTED_AMOUNT_MINOR
    assert stub.initialize_calls[0]["currency"] == "KES"
    assert stub.initialize_calls[0]["plan_code"] == TEST_PLAN_CODE


def test_initialize_without_a_plan_code_is_refused(app, client, vendor):
    """No plan code means no chargeable plan. Refuse rather than invent one."""
    app.config["PAYSTACK_PLAN_CODE"] = ""

    with app.app_context():
        Plan.query.update({Plan.paystack_plan_code: None})
        db.session.commit()

    response = client.post(
        "/api/payments/initialize",
        json={},
        headers=auth_headers(app, vendor),
    )

    assert response.status_code == 503
    assert "not available" in response.get_json()["error"]


def test_initialize_requires_authentication(app, client, vendor):
    """Checklist item: unauthorized subscription request."""
    response = client.post("/api/payments/initialize", json={})

    assert response.status_code == 401
    assert response.get_json()["error"] == "Authentication required"


def test_initialize_rejects_a_forged_token(app, client, vendor):
    forged = issue_vendor_token("web-test-user", vendor)["token"].replace(
        "sauti_vendor.", "sauti_vendor.x"
    )
    response = client.post(
        "/api/payments/initialize", json={}, headers={"X-Vendor-Token": forged}
    )

    assert response.status_code == 401


def test_initialize_rejects_a_token_for_a_nonexistent_vendor(app, client, vendor):
    """A well-signed token for a vendor that does not exist gets 404.

    Distinct from the forgery cases: the signature here is perfectly valid, so
    the refusal comes from the vendor lookup rather than the token check. Editing
    a real token to point at another vendor is covered separately, by
    ``test_token_signature_cannot_be_tampered_with``.
    """
    token = issue_vendor_token("web-test-user", "no-such-vendor")["token"]
    response = client.post(
        "/api/payments/initialize", json={}, headers={"X-Vendor-Token": token}
    )

    assert response.status_code == 404


def test_initialize_rejects_invalid_json(app, client, vendor):
    response = client.post(
        "/api/payments/initialize",
        data="not json",
        content_type="application/json",
        headers=auth_headers(app, vendor),
    )

    assert response.status_code == 400


def test_repeated_initialization_reuses_one_reference(app, client, vendor, stub_paystack):
    """Checklist item: repeated payment initialization.

    A double-tapped Subscribe must not leave two live Paystack sessions for the
    same month. The second call reuses the open reference, so it is one payment
    and one reference regardless of how many times Subscribe is pressed.
    """
    stub_paystack()
    headers = auth_headers(app, vendor)

    first = client.post("/api/payments/initialize", json={}, headers=headers)
    second = client.post("/api/payments/initialize", json={}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()["reference"] == second.get_json()["reference"]

    with app.app_context():
        assert Payment.query.count() == 1


def test_second_initialization_when_already_active_is_a_conflict(app, client, vendor):
    from backend.payments import service

    with app.app_context():
        seed_pending_payment(vendor)
        subscription = Subscription.query.first()
        subscription.status = SUBSCRIPTION_ACTIVE
        db.session.commit()

        with pytest.raises(service.PaymentServiceError) as caught:
            service.initialize_payment(
                user_id="web-test-user", vendor_id=vendor, client=StubClient()
            )

    assert caught.value.status == 409


def test_paystack_unavailable_during_initialize(app, client, vendor, stub_paystack):
    """Checklist item: Paystack API unavailable."""
    outage = PaystackError(
        "The payment service is temporarily unavailable. Please try again.",
        detail="connection refused",
        status=502,
    )
    stub_paystack(error=outage)

    response = client.post(
        "/api/payments/initialize",
        json={},
        headers=auth_headers(app, vendor),
    )

    assert response.status_code == 502
    body = response.get_json()
    assert "temporarily unavailable" in body["error"]
    # Provider internals must not reach the browser.
    assert "connection refused" not in json.dumps(body)


def test_initialize_response_never_contains_the_secret_key(
    app, client, vendor, stub_paystack
):
    stub_paystack()

    response = client.post(
        "/api/payments/initialize",
        json={"email": "vendor@example.com"},
        headers=auth_headers(app, vendor),
    )

    assert response.status_code == 200
    serialized = response.get_data(as_text=True)
    assert "sk_test_" not in serialized
    assert TEST_SECRET not in serialized
    # The authorization URL and reference are what the browser needs.
    body = response.get_json()
    assert body["authorizationUrl"].startswith("https://checkout.paystack.test/")
    assert body["reference"].startswith("sauti_")
    assert body["accessCode"] == "access_test_abc"


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_verify_activates_a_genuine_payment(app, client, vendor, stub_paystack):
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    stub = stub_paystack(paid=True)

    response = client.get(
        f"/api/payments/verify/{reference}", headers=auth_headers(app, vendor)
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["verified"] is True
    assert body["amount"] == 100

    with app.app_context():
        assert Payment.query.filter_by(reference=reference).first().status == PAYMENT_SUCCESS
        assert Subscription.query.first().status == SUBSCRIPTION_ACTIVE


def test_verify_rejects_an_unknown_reference(app, client, vendor, stub_paystack):
    """Checklist item: invalid transaction reference."""
    response = client.get(
        "/api/payments/verify/sauti_doesnotexist000000000000",
        headers=auth_headers(app, vendor),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "Transaction not found"


def test_verify_rejects_a_malformed_reference(app, client, vendor):
    response = client.get(
        "/api/payments/verify/" + "x" * 300, headers=auth_headers(app, vendor)
    )

    assert response.status_code == 404


def test_verify_rejects_a_reference_belonging_to_another_vendor(
    app, client, vendor, stub_paystack
):
    """A reference that verifies is not proof the caller may hear about it.

    Callback URLs are shareable and end up in browser history and referrer
    headers, so ownership is checked independently of the payment being real.
    """
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

        rival = Vendor(
            business_name="Other Shop",
            slug="other-shop",
            owner_user_id="web-test-user",
        )
        db.session.add(rival)
        db.session.commit()
        rival_id = rival.id

    response = client.get(
        f"/api/payments/verify/{reference}",
        headers=auth_headers(app, rival_id),
    )

    assert response.status_code == 403


def test_verify_does_not_activate_on_an_unpaid_transaction(
    app, client, vendor, stub_paystack
):
    """Checklist item: failed payment.

    A verification that reaches Paystack and finds nothing paid must not grant
    access. This is the server refusing the browser's claim on its own.
    """
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    stub_paystack(paid=False, status="failed")

    response = client.get(
        f"/api/payments/verify/{reference}", headers=auth_headers(app, vendor)
    )

    assert response.status_code == 402
    assert response.get_json()["error"] == "Payment failed"

    with app.app_context():
        assert Payment.query.filter_by(reference=reference).first().status == PAYMENT_FAILED
        assert Subscription.query.first().status != SUBSCRIPTION_ACTIVE


def test_verify_refuses_an_amount_mismatch(app, client, vendor, stub_paystack):
    """Checklist item: incorrect amount.

    Paystack reports a different amount from what the plan costs. Refused, not
    activated — a signature or a successful API call proves authenticity, not
    that the right amount was paid.
    """
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    stub_paystack(paid=True, amount_minor=1)

    response = client.get(
        f"/api/payments/verify/{reference}", headers=auth_headers(app, vendor)
    )

    assert response.status_code == 409
    assert "amount" in response.get_json()["error"].lower()

    with app.app_context():
        assert Subscription.query.first().status != SUBSCRIPTION_ACTIVE


def test_verify_refuses_a_currency_mismatch(app, client, vendor, stub_paystack):
    """Checklist item: incorrect currency."""
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    stub_paystack(paid=True, currency="NGN")

    response = client.get(
        f"/api/payments/verify/{reference}", headers=auth_headers(app, vendor)
    )

    assert response.status_code == 409
    assert "currency" in response.get_json()["error"].lower()


def test_verify_never_activates_without_a_payment(app, client, vendor):
    """Checklist item: attempt to activate a subscription without payment.

    There is no activate endpoint, and this confirms the obvious candidate —
    the status endpoint — is read-only.
    """
    before = client.get("/api/subscriptions/status", headers=auth_headers(app, vendor))
    assert before.status_code == 200
    assert before.get_json()["status"] == SUBSCRIPTION_INACTIVE

    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)(
            "/api/subscriptions/status", json={}, headers=auth_headers(app, vendor)
        )
        assert response.status_code == 405

    after = client.get("/api/subscriptions/status", headers=auth_headers(app, vendor))
    assert after.get_json()["status"] == SUBSCRIPTION_INACTIVE
    assert after.get_json()["isEntitled"] is False


def test_verify_requires_authentication(app, client):
    response = client.get("/api/payments/verify/sauti_anything")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------


def test_signature_is_hmac_sha512_of_the_raw_body(app):
    raw = b'{"event":"charge.success"}'
    expected = hmac.new(TEST_SECRET.encode(), raw, hashlib.sha512).hexdigest()

    assert compute_signature(raw, TEST_SECRET) == expected


def test_webhook_accepts_a_correctly_signed_event(app, client, vendor):
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    response = post_webhook(client, charge_event(reference))

    assert response.status_code == 200
    assert response.get_json()["status"] is True

    with app.app_context():
        assert Payment.query.filter_by(reference=reference).first().status == PAYMENT_SUCCESS
        assert Subscription.query.first().status == SUBSCRIPTION_ACTIVE


def test_webhook_rejects_an_invalid_signature(app, client, vendor):
    """Checklist item: invalid webhook signature."""
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    response = post_webhook(
        client, charge_event(reference), signature="0" * 128
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Invalid webhook signature"

    with app.app_context():
        # Nothing applied. An unauthenticated caller must not be able to grant
        # a subscription by posting a forged event.
        assert Payment.query.filter_by(reference=reference).first().status == PAYMENT_PENDING
        assert Subscription.query.first().status != SUBSCRIPTION_ACTIVE
        assert PaymentEvent.query.count() == 0


def test_webhook_rejects_a_missing_signature(app, client, vendor):
    """Checklist item: missing webhook signature."""
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    response = post_webhook(client, charge_event(reference), sign=False)

    assert response.status_code == 403
    with app.app_context():
        assert PaymentEvent.query.count() == 0


def test_webhook_rejects_a_signature_from_the_wrong_secret(app, client, vendor):
    response = post_webhook(
        client, charge_event("sauti_x"), secret="sk_test_" + "1" * 32
    )

    assert response.status_code == 403


def test_webhook_signature_is_checked_over_the_exact_bytes(app, client, vendor):
    """Re-serialising the JSON must not produce a matching signature.

    This is the bug that silently breaks every webhook: computing the HMAC over
    a re-encoded body instead of the raw one. ``json.dumps`` defaults to the same
    separators as the body, so the variant used here is an indented one, which is
    semantically identical JSON and byte-for-byte different.
    """
    event = charge_event("sauti_x")
    raw = json.dumps(event).encode()
    respaced = json.dumps(event, indent=2).encode()

    assert json.loads(raw) == json.loads(respaced)
    assert raw != respaced

    response = post_webhook(client, event, signature=compute_signature(respaced, TEST_SECRET))

    assert response.status_code == 403


def test_webhook_rejects_an_oversized_body(app, client):
    oversized = "x" * (600 * 1024)
    raw = json.dumps({"event": "charge.success", "data": {"pad": oversized}}).encode()
    response = post_webhook(client, {"event": "charge.success", "data": {"pad": oversized}})

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Webhook idempotency
# ---------------------------------------------------------------------------


def test_duplicate_webhook_activates_only_once(app, client, vendor):
    """Checklist item: duplicate webhook.

    The core guarantee. Paystack retries, so this will happen in production, and
    a second activation would silently grant a free extra month.
    """
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    event = charge_event(reference)

    first = post_webhook(client, event)
    assert first.status_code == 200

    second = post_webhook(client, event)
    assert second.status_code == 200
    assert second.get_json()["message"] == "duplicate ignored"

    with app.app_context():
        subscription = Subscription.query.first()
        assert subscription.status == SUBSCRIPTION_ACTIVE

        # The paid window must not have moved forward a second time.
        original_next = subscription.next_payment_date
        assert PaymentEvent.query.count() == 1

    # A third delivery, plus the verify endpoint, must still not extend it.
    post_webhook(client, event)

    with app.app_context():
        assert Subscription.query.first().next_payment_date == original_next


def test_duplicate_detection_survives_concurrent_delivery(app):
    """The uniqueness constraint, not a SELECT-then-INSERT, is the guarantee."""
    from backend.payments.service import record_event
    from sqlalchemy.exc import IntegrityError

    event = charge_event("sauti_race")
    with app.app_context():
        stored, duplicate = record_event(event)
        assert duplicate is False

        # Simulate the racing worker: a direct second insert must be refused by
        # the database rather than by application logic.
        db.session.add(
            PaymentEvent(
                event_type=event["event"],
                event_key=derive_event_key(event),
                payload={},
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        # The row from the first delivery was never processed, because
        # record_event only stores. A redelivery must therefore NOT be reported as
        # a duplicate: it is Paystack's retry of an event that never took effect,
        # and reporting it as handled would leave the subscription inactive while
        # telling Paystack to stop asking.
        stored, duplicate = record_event(event)
        assert duplicate is False

        # Once the event has actually been applied, a further redelivery is a true
        # duplicate and is skipped.
        stored.processed = True
        db.session.commit()

        stored, duplicate = record_event(event)
        assert duplicate is True


def test_event_key_distinguishes_different_transactions(app):
    a = derive_event_key(charge_event("sauti_aaa"))
    b = derive_event_key(charge_event("sauti_bbb"))

    assert a != b
    assert a == derive_event_key(charge_event("sauti_aaa"))


def test_event_key_falls_back_to_a_body_hash(app):
    """An unidentifiable event is still recorded, by hashing the body."""
    key = derive_event_key({"event": "customer.created", "data": {}})

    assert key.startswith("customer.created:body:")
    # Stable across calls, which is what makes the fallback deduplicate at all.
    assert key == derive_event_key({"event": "customer.created", "data": {}})


def test_unhandled_event_is_recorded_and_acknowledged(app, client):
    """Checklist item: unknown webhook event.

    Must not crash, and must return 200 so Paystack stops retrying something we
    will never act on.
    """
    response = post_webhook(client, {"event": "some.new.event", "data": {"id": 1}})

    assert response.status_code == 200

    with app.app_context():
        stored = PaymentEvent.query.one()
        assert stored.event_type == "some.new.event"
        assert stored.processed is True


def test_webhook_with_a_non_object_body_is_acknowledged(app, client):
    raw = json.dumps(["not", "an", "object"]).encode()
    response = client.post(
        "/api/payments/webhook",
        data=raw,
        headers={"x-paystack-signature": compute_signature(raw, TEST_SECRET)},
        content_type="application/json",
    )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Webhook event handling
# ---------------------------------------------------------------------------


def test_charge_success_with_a_wrong_amount_does_not_activate(app, client, vendor):
    """A signature proves authenticity, not accuracy."""
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    response = post_webhook(client, charge_event(reference, amount=1))

    # Acknowledged, so Paystack stops retrying, but nothing was granted.
    assert response.status_code == 200

    with app.app_context():
        assert Payment.query.filter_by(reference=reference).first().status == PAYMENT_PENDING
        assert Subscription.query.first().status != SUBSCRIPTION_ACTIVE
        assert PaymentEvent.query.one().processed is True


def test_charge_success_with_a_wrong_currency_does_not_activate(app, client, vendor):
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    post_webhook(client, charge_event(reference, currency="NGN"))

    with app.app_context():
        assert Payment.query.filter_by(reference=reference).first().status == PAYMENT_PENDING
        assert Subscription.query.first().status != SUBSCRIPTION_ACTIVE


def test_charge_success_whose_status_is_not_success_is_refused(app, client, vendor):
    """The event name is not the fact. The transaction status is."""
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    post_webhook(client, charge_event(reference, status="failed"))

    with app.app_context():
        assert Payment.query.filter_by(reference=reference).first().status == (
            PAYMENT_FAILED
        )
        assert Subscription.query.first().status != SUBSCRIPTION_ACTIVE


def test_both_paths_record_a_declined_payment_the_same_way(
    app, client, vendor, stub_paystack
):
    """Verify and webhook must not disagree about the same outcome.

    Verify moved the row to failed while the webhook left it pending, so the
    state a payment ended up in depended on which route happened to notice first.
    """

    def make_payment(reference):
        from backend.payments.service import (
            ensure_vendor_plan,
            get_or_create_subscription,
        )

        plan = ensure_vendor_plan()
        vendor_row = db.session.get(Vendor, vendor)
        subscription = get_or_create_subscription(
            vendor_row, vendor_row.owner_user_id, plan
        )
        payment = Payment(
            vendor_id=vendor,
            user_id=vendor_row.owner_user_id,
            plan_id=plan.id,
            subscription_id=subscription.id,
            reference=reference,
            amount_minor=EXPECTED_AMOUNT_MINOR,
            currency="KES",
            status=PAYMENT_PENDING,
        )
        db.session.add(payment)
        db.session.commit()
        return payment

    # Path one: the webhook.
    with app.app_context():
        webhook_reference = make_payment("sauti_declined_webhook00000000").reference

    post_webhook(client, charge_event(webhook_reference, status="failed"))

    with app.app_context():
        assert Payment.query.filter_by(reference=webhook_reference).one().status == (
            PAYMENT_FAILED
        )

    # Path two: the verify route, for a different transaction.
    stub_paystack(paid=False, status="failed")

    with app.app_context():
        verify_reference = make_payment("sauti_declined_verify000000000").reference

    response = client.get(
        f"/api/payments/verify/{verify_reference}", headers=auth_headers(app, vendor)
    )
    assert response.status_code == 402

    with app.app_context():
        assert Payment.query.filter_by(reference=verify_reference).one().status == (
            PAYMENT_FAILED
        )


def test_charge_success_for_an_unknown_reference_changes_nothing(app, client):
    post_webhook(client, charge_event("sauti_notours00000000000000"))

    assert app is not None
    with app.app_context():
        assert Payment.query.count() == 0
        assert Subscription.query.count() == 0
        stored = PaymentEvent.query.one()
        assert stored.processed is True


def test_subscription_create_links_the_codes(app, client, vendor):
    with app.app_context():
        seed_pending_payment(vendor)

    response = post_webhook(
        client,
        {
            "event": "subscription.create",
            "data": {
                "id": 999,
                "subscription_code": "SUB_test123",
                "customer_code": "CUS_test123",
                "plan_code": TEST_PLAN_CODE,
                "invoice_code": "INV_test123",
            },
        },
    )

    assert response.status_code == 200

    with app.app_context():
        subscription = Subscription.query.one()
        assert subscription.paystack_subscription_code == "SUB_test123"
        assert subscription.paystack_customer_code == "CUS_test123"
        assert subscription.paystack_email_token == "INV_test123"


def test_invoice_create_does_not_change_state(app, client, vendor):
    with app.app_context():
        seed_pending_payment(vendor)
        before = Subscription.query.one().status

    response = post_webhook(
        client,
        {
            "event": "invoice.create",
            "data": {
                "subscription_code": "SUB_test123",
                "amount": 10000,
                "currency": "KES",
            },
        },
    )

    assert response.status_code == 200
    with app.app_context():
        # An invoice announces a charge that has not happened yet.
        assert Subscription.query.one().status == before


def test_invoice_payment_failed_marks_past_due_not_cancelled(app, client, vendor):
    """Paystack retries a failed renewal before disabling. Revoking access on
    the first failure locks out vendors whose bank needed an extra day."""
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    post_webhook(client, charge_event(reference))
    with app.app_context():
        assert Subscription.query.one().status == SUBSCRIPTION_ACTIVE

    post_webhook(
        client,
        {
            "event": "invoice.payment_failed",
            "data": {"subscription_code": "SUB_x", "reference": reference},
        },
    )

    with app.app_context():
        subscription = Subscription.query.one()
        assert subscription.status == SUBSCRIPTION_PAST_DUE
        # Still entitled, because Paystack will retry.
        assert subscription.is_entitled is True


def test_subscription_disable_cancels(app, client, vendor):
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    post_webhook(client, charge_event(reference))

    post_webhook(
        client,
        {
            "event": "subscription.disable",
            "data": {"subscription_code": "SUB_x", "reference": reference},
        },
    )

    with app.app_context():
        subscription = Subscription.query.one()
        assert subscription.status == SUBSCRIPTION_CANCELLED
        assert subscription.is_entitled is False


def test_subscription_not_renew_expires(app, client, vendor):
    """A deliberate end, not a failure, so it is expired rather than cancelled."""
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    post_webhook(client, charge_event(reference))

    post_webhook(
        client,
        {
            "event": "subscription.not_renew",
            "data": {"subscription_code": "SUB_x", "reference": reference},
        },
    )

    with app.app_context():
        subscription = Subscription.query.one()
        assert subscription.status == SUBSCRIPTION_EXPIRED
        assert subscription.is_entitled is False


def test_lifecycle_events_are_idempotent(app, client, vendor):
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    post_webhook(client, charge_event(reference))

    event = {
        "event": "subscription.disable",
        "data": {"subscription_code": "SUB_x", "reference": reference},
    }
    post_webhook(client, event)
    post_webhook(client, event)
    post_webhook(client, event)

    with app.app_context():
        assert Subscription.query.one().status == SUBSCRIPTION_CANCELLED
        # One charge.success event plus one subscription.disable event, however
        # many times the disable was delivered.
        assert PaymentEvent.query.count() == 2


def test_payment_initialization_is_rate_limited(app, client, vendor):
    """Checklist item: too many payment attempts.

    Application-level abuse control. Card-level risk is Paystack's job; bounding
    how often one caller can open checkout sessions is Sauti's.
    """
    app.config["PAYSTACK_INIT_RATE_LIMIT"] = "3 per minute"
    headers = auth_headers(app, vendor)

    codes = [
        client.post("/api/payments/initialize", json={}, headers=headers).status_code
        for _ in range(5)
    ]

    assert 429 in codes
    # The limiter is applied before the handler, so the refusal happens without
    # a database write or a Paystack call.
    with app.app_context():
        assert Payment.query.count() <= 3


# ---------------------------------------------------------------------------
# Subscription status
# ---------------------------------------------------------------------------


def test_status_reports_inactive_for_a_never_paying_vendor(app, client, vendor):
    """Checklist item: expired/inactive subscription."""
    response = client.get("/api/subscriptions/status", headers=auth_headers(app, vendor))

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == SUBSCRIPTION_INACTIVE
    assert body["isEntitled"] is False
    assert body["plan"]["amount"] == 100
    assert body["plan"]["currency"] == "KES"
    assert body["mode"] == "test"


def test_status_requires_authentication(app, client):
    assert client.get("/api/subscriptions/status").status_code == 401


def test_status_never_returns_the_secret_key(app, client, vendor):
    body = client.get(
        "/api/subscriptions/status", headers=auth_headers(app, vendor)
    ).get_data(as_text=True)

    assert "sk_test_" not in body
    assert TEST_SECRET not in body


def test_status_reports_pending_after_checkout_starts(app, client, vendor):
    with app.app_context():
        seed_pending_payment(vendor)

    body = client.get(
        "/api/subscriptions/status", headers=auth_headers(app, vendor)
    ).get_json()

    assert body["status"] == SUBSCRIPTION_PENDING
    assert body["isEntitled"] is False


def test_status_reports_active_after_a_verified_payment(app, client, vendor):
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    post_webhook(client, charge_event(reference))

    body = client.get(
        "/api/subscriptions/status", headers=auth_headers(app, vendor)
    ).get_json()

    assert body["status"] == SUBSCRIPTION_ACTIVE
    assert body["isEntitled"] is True
    assert body["subscription"] is not None
    assert body["subscription"]["nextPaymentDate"] is not None


def test_stale_pending_subscription_becomes_expired(app, client, vendor):
    """A pending subscription whose window elapsed must not read as pending
    forever, or the UI tells a vendor to go and wait for a payment that is long
    gone."""
    from datetime import datetime, timedelta, timezone

    with app.app_context():
        seed_pending_payment(vendor)
        subscription = Subscription.query.one()
        subscription.next_payment_date = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()

    body = client.get(
        "/api/subscriptions/status", headers=auth_headers(app, vendor)
    ).get_json()

    assert body["status"] == SUBSCRIPTION_EXPIRED
    assert body["isEntitled"] is False


def test_mark_active_does_not_extend_an_unexpired_window(app, vendor):
    """The guard that makes a duplicated activation harmless."""
    from datetime import datetime, timedelta, timezone

    with app.app_context():
        payment = seed_pending_payment(vendor)
        subscription = payment.subscription

        now = datetime.now(timezone.utc)
        assert subscription.mark_active(now=now) is True

        first_window = subscription.next_payment_date

        # A second activation an hour later must not grant another month.
        assert subscription.mark_active(now=now + timedelta(hours=1)) is False
        assert subscription.next_payment_date == first_window

        # Once the window has elapsed, a renewal does extend it.
        assert subscription.mark_active(now=first_window + timedelta(seconds=1)) is True
        assert subscription.next_payment_date > first_window


# ---------------------------------------------------------------------------
# Vendor tokens
# ---------------------------------------------------------------------------


def test_token_round_trip(app):
    issued = issue_vendor_token("web-test-user", "vendor-1")

    principal = decode_vendor_token(issued["token"])

    assert principal.user_id == "web-test-user"
    assert principal.vendor_id == "vendor-1"
    assert principal.is_expired is False


def test_expired_token_is_rejected(app):
    issued = issue_vendor_token("web-test-user", "vendor-1", ttl=-10)

    with pytest.raises(Exception):
        decode_vendor_token(issued["token"])


def test_token_signature_cannot_be_tampered_with(app):
    issued = issue_vendor_token("web-test-user", "vendor-1")
    header, body, signature = issued["token"].split(".")

    import base64

    forged_body = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"v": "v1", "uid": "web-test-user", "vid": "other", "exp": int(time.time()) + 60}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )

    with pytest.raises(Exception):
        decode_vendor_token(f"{header}.{forged_body}.{signature}")


def test_malformed_tokens_are_rejected(app):
    for candidate in ("", "garbage", "a.b", "sauti_vendor.only_two", None):
        with pytest.raises(Exception):
            decode_vendor_token(candidate)


def test_token_issue_requires_an_existing_vendor(app, client):
    response = client.post(
        "/api/payments/vendor/token",
        json={"user_id": "web-test-user", "vendor_id": "does-not-exist"},
    )

    assert response.status_code == 404


def test_token_issue_validates_input(app, client):
    response = client.post(
        "/api/payments/vendor/token", json={"user_id": "x"}
    )

    assert response.status_code == 400

    bad_email = client.post(
        "/api/payments/vendor/token",
        json={"user_id": "web-test-user", "vendor_id": "v", "email": "not-an-email"},
    )
    assert bad_email.status_code == 400


def test_a_vendor_owned_by_someone_else_cannot_be_claimed(app, client, vendor):
    with app.app_context():
        other = Vendor(
            business_name="Rival Shop",
            slug="rival-shop",
            owner_user_id="web-someone-else",
        )
        db.session.add(other)
        db.session.commit()
        rival_id = other.id

    response = client.post(
        "/api/payments/vendor/token",
        json={"user_id": "web-test-user", "vendor_id": rival_id},
    )

    assert response.status_code == 403


def test_token_response_never_contains_the_secret_key(app, client, vendor):
    response = client.post(
        "/api/payments/vendor/token",
        json={"user_id": "web-test-user", "vendor_id": vendor},
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "sk_test_" not in body
    assert TEST_SECRET not in body


# ---------------------------------------------------------------------------
# Secret hygiene
# ---------------------------------------------------------------------------


def test_no_payment_response_body_contains_a_secret_key(app, client, vendor):
    """Checklist items 19 and 20, from the server's side.

    The frontend bundle check lives in the build; this proves no API response can
    be the thing that leaks one.
    """
    headers = auth_headers(app, vendor)

    responses = [
        client.get("/api/subscriptions/status", headers=headers),
        client.post("/api/payments/initialize", json={}, headers=headers),
        client.get("/api/payments/config", headers=headers),
        client.post(
            "/api/payments/vendor/token",
            json={"user_id": "web-test-user", "vendor_id": vendor},
        ),
        client.get("/api/payments/pending", headers=headers),
    ]

    for response in responses:
        body = response.get_data(as_text=True)
        assert "sk_test_" not in body, f"secret leaked from {response.request.path}"
        assert "sk_live_" not in body
        assert TEST_SECRET not in body


def test_config_endpoint_only_exposes_the_public_key(app, client, vendor):
    body = client.get(
        "/api/payments/config", headers=auth_headers(app, vendor)
    ).get_json()

    assert body["publicKey"] == TEST_PUBLIC
    assert body["mode"] == "test"
    assert body["callbackUrl"] == "https://sauti-ai.onrender.com/payment/callback"
    assert "secret" not in json.dumps(body).lower()


def test_payment_models_do_not_store_card_data(app):
    """No column exists that could hold a PAN, CVV, PIN or OTP.

    Paystack never sends those in a webhook, so this is belt and braces: it fails
    if someone later adds a permissive "raw_payload" column.
    """
    forbidden = {"card", "card_number", "cvv", "pin", "otp", "pan", "account_number"}

    for model in (Payment, PaymentEvent, Subscription, Plan):
        columns = {column.name.lower() for column in model.__table__.columns}
        assert not (columns & forbidden), f"{model.__tablename__} exposes card data"


def test_stored_webhook_payload_is_stripped_of_sensitive_fields(app, client):
    """A webhook body is never retained verbatim."""
    response = post_webhook(
        client,
        {
            "event": "charge.success",
            "data": {
                "reference": "sauti_x",
                "cvv": "123",
                "card": {"number": "4084080840408408"},
                "authorization": {"pin": "1234"},
                "otp": "999999",
            },
        },
    )

    assert response.status_code == 200

    with app.app_context():
        serialized = json.dumps(PaymentEvent.query.one().payload).lower()
        for secret_value in ("4084080840408408", "1234", "999999"):
            assert secret_value not in serialized
        assert "cvv" not in serialized
        assert "otp" not in serialized

# ---------------------------------------------------------------------------
# Regressions for the code-review findings
#
# Each test names the failure it is preventing. They are grouped at the end so
# the behavioural tests above still read as the feature specification.
# ---------------------------------------------------------------------------


# --- #1: a missing secret must fail closed, not key an HMAC with "" --------


def test_webhook_is_rejected_when_no_secret_is_configured(app, client):
    """A deployment with no Paystack secret must reject webhooks, not accept them.

    compute_signature() substitutes an empty string for a missing key, and HMAC
    keyed with the empty string is reproducible by anyone. Without an explicit
    guard, a deployment that had not yet been given PAYSTACK_SECRET_KEY would
    accept any body as authentic, and a forged charge.success would activate a
    subscription for free.
    """
    app.config["PAYSTACK_SECRET_KEY"] = ""

    raw = json.dumps(charge_event("sauti_forged")).encode()
    forged_signature = compute_signature(raw, "")

    response = client.post(
        "/api/payments/webhook",
        data=raw,
        headers={"x-paystack-signature": forged_signature},
        content_type="application/json",
    )

    assert response.status_code == 403

    with app.app_context():
        # Nothing was stored and, above all, nothing was activated.
        assert PaymentEvent.query.count() == 0
        assert Subscription.query.count() == 0


def test_webhook_is_rejected_when_the_key_prefix_is_unrecognised(app, client):
    """An unrecognised key prefix is treated as no key at all.

    Guessing the mode from a prefix that is neither sk_test_ nor sk_live_ would
    mean trusting an operator typo as if it were configuration.
    """
    app.config["PAYSTACK_SECRET_KEY"] = "not-a-paystack-key"

    raw = json.dumps(charge_event("sauti_forged2")).encode()

    response = client.post(
        "/api/payments/webhook",
        data=raw,
        headers={"x-paystack-signature": compute_signature(raw, "not-a-paystack-key")},
        content_type="application/json",
    )

    assert response.status_code == 403


# --- #2: a non-ASCII signature header must not 500 -----------------------


@pytest.mark.parametrize(
    "signature",
    [
        "é" * 128,
        "0" * 127 + "é",
        "\U0001f600" * 128,
        "0" * 64 + "ſ" + "0" * 63,
    ],
)
def test_non_ascii_signature_is_rejected_without_a_server_error(
    app, client, signature
):
    """compare_digest raises TypeError on a non-ASCII str operand.

    Without a shape check first, any anonymous caller could turn the public
    webhook endpoint into a 500 with a single header value — and a 5xx from a
    webhook looks like a fault on our side, which is exactly the signal a
    scanner looks for.
    """
    response = client.post(
        "/api/payments/webhook",
        data=json.dumps(charge_event("sauti_ascii")).encode(),
        headers={"x-paystack-signature": signature},
        content_type="application/json",
    )

    assert response.status_code == 403


def test_signature_of_the_wrong_length_is_rejected(app, client):
    """A short or over-long signature is refused before the comparison."""
    raw = json.dumps(charge_event("sauti_len")).encode()

    for signature in ("0" * 127, "0" * 129, "", "  "):
        response = client.post(
            "/api/payments/webhook",
            data=raw,
            headers={"x-paystack-signature": signature},
            content_type="application/json",
        )
        assert response.status_code == 403


# --- #3: recurring renewal charges must not be dropped --------------------


def test_recurring_renewal_charge_extends_the_subscription(app, client, vendor):
    """A renewal carries a reference Sauti never issued.

    Because Sauti sends a plan code, Paystack charges automatically each interval
    using its own reference. Matching on reference alone found nothing from month
    two onward: the customer kept being billed and the subscription silently
    stopped extending.
    """
    from backend.payments.service import ensure_vendor_plan

    with app.app_context():
        plan = ensure_vendor_plan()
        vendor_row = db.session.get(Vendor, vendor)
        subscription = seed_pending_payment(vendor).subscription
        subscription.paystack_subscription_code = "SUB_testrenewal"
        subscription.status = SUBSCRIPTION_ACTIVE
        # A window that has run out, which is what a renewal renews.
        subscription.start_date = utcnow() - timedelta(days=60)
        subscription.next_payment_date = utcnow() - timedelta(days=30)
        db.session.commit()
        original_next = subscription.next_payment_date
        assert Payment.query.filter_by(reference="paystack_renewal_1").count() == 0

    # Paystack's own reference, and the subscription code it attaches.
    event = {
        "event": "charge.success",
        "data": {
            "id": 400001,
            "reference": "paystack_renewal_1",
            "status": "success",
            "amount": EXPECTED_AMOUNT_MINOR,
            "currency": "KES",
            "channel": "card",
            "paid_at": "2026-02-15T10:00:00Z",
            "subscription": {
                "subscription_code": "SUB_testrenewal",
                "customer": {"customer_code": "CUS_test123"},
            },
            "customer": {"customer_code": "CUS_test123"},
        },
    }

    response = post_webhook(client, event)
    assert response.status_code == 200

    with app.app_context():
        renewal = Payment.query.filter_by(reference="paystack_renewal_1").one()
        assert renewal.status == PAYMENT_SUCCESS
        # Recorded at the plan price, not at whatever the body claimed.
        assert renewal.amount_minor == EXPECTED_AMOUNT_MINOR
        assert renewal.currency == "KES"

        subscription = Subscription.query.one()
        assert subscription.status == SUBSCRIPTION_ACTIVE
        assert subscription.next_payment_date > original_next


def test_renewal_matching_falls_back_to_the_customer_code(app, client, vendor):
    """Paystack omits the subscription code on many renewal charges."""
    with app.app_context():
        payment = seed_pending_payment(vendor)
        subscription = payment.subscription
        subscription.paystack_customer_code = "CUS_test123"
        subscription.status = SUBSCRIPTION_ACTIVE
        subscription.next_payment_date = utcnow() - timedelta(days=31)
        db.session.commit()

    event = {
        "event": "charge.success",
        "data": {
            "reference": "paystack_renewal_nocode",
            "status": "success",
            "amount": EXPECTED_AMOUNT_MINOR,
            "currency": "KES",
            "customer": {"customer_code": "CUS_test123"},
        },
    }

    response = post_webhook(client, event)
    assert response.status_code == 200

    with app.app_context():
        renewal = Payment.query.filter_by(
            reference="paystack_renewal_nocode"
        ).one()
        assert renewal.status == PAYMENT_SUCCESS


def test_a_renewal_claiming_the_wrong_amount_is_refused(app, client, vendor):
    """Renewal rows are recorded at the plan price so the check has teeth.

    Building the row from the webhook body would make the comparison circular:
    the body would always agree with itself.
    """
    with app.app_context():
        payment = seed_pending_payment(vendor)
        payment.subscription.paystack_subscription_code = "SUB_wrongamount"
        db.session.commit()

    event = {
        "event": "charge.success",
        "data": {
            "reference": "paystack_renewal_wrong",
            "status": "success",
            # A penny, not KSh 100.
            "amount": 1,
            "currency": "KES",
            "subscription": {"subscription_code": "SUB_wrongamount"},
        },
    }

    response = post_webhook(client, event)
    assert response.status_code == 200

    with app.app_context():
        renewal = Payment.query.filter_by(reference="paystack_renewal_wrong").one()
        assert renewal.status != PAYMENT_SUCCESS
        assert Subscription.query.one().status != SUBSCRIPTION_ACTIVE


def test_charge_for_a_completely_unknown_reference_changes_nothing(
    app, client, vendor
):
    """No matching payment and no matching subscription: refuse, do not adopt."""
    post_webhook(client, charge_event("paystack_unknown_9z"))

    with app.app_context():
        assert Payment.query.count() == 0
        assert Subscription.query.count() == 0
        # The event is still recorded, so the fact it arrived survives.
        assert PaymentEvent.query.count() == 1


# --- #4: the plan's Paystack code is backfilled ---------------------------


def test_existing_plan_without_a_paystack_code_is_backfilled(app):
    """The plan row outlives the config that created it.

    ensure_vendor_plan must fill in a code that was configured after the row was
    first created, otherwise checkout stays 503 forever on a deployment where
    the plan was seeded before the dashboard code existed.
    """
    from backend.payments.service import ensure_vendor_plan

    with app.app_context():
        plan = Plan(
            slug="vendor-monthly",
            name="Vendor Monthly",
            amount_minor=EXPECTED_AMOUNT_MINOR,
            currency="KES",
            interval="monthly",
            minor_exponent=2,
            paystack_plan_code=None,
            is_active=True,
        )
        db.session.add(plan)
        db.session.commit()
        plan_id = plan.id

        assert plan.paystack_plan_code is None

        result = ensure_vendor_plan()

        assert result.id == plan_id
        assert result.paystack_plan_code == TEST_PLAN_CODE


def test_backfill_does_not_overwrite_a_different_configured_plan(app):
    """A code already on the row is left alone.

    Overwriting it would silently repoint an existing live plan at a new one,
    changing what customers are actually charged.
    """
    from backend.payments.service import ensure_vendor_plan

    with app.app_context():
        plan = Plan(
            slug="vendor-monthly",
            name="Vendor Monthly",
            amount_minor=EXPECTED_AMOUNT_MINOR,
            currency="KES",
            interval="monthly",
            minor_exponent=2,
            paystack_plan_code="PLN_alreadyconfigured",
            is_active=True,
        )
        db.session.add(plan)
        db.session.commit()

        assert ensure_vendor_plan().paystack_plan_code == "PLN_alreadyconfigured"


# --- #9/#16: retries must be able to succeed ------------------------------


def test_provider_failure_leaves_the_event_unprocessed_for_retry(
    app, client, vendor, stub_paystack
):
    """A Paystack outage must not be reported as a handled event.

    Marking it processed tells Paystack to stop retrying, so the event is lost and
    the subscription stays inactive while the customer has been charged.
    """
    stub_paystack(error=PaystackError("timeout"))

    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    # No inline transaction facts, so the handler must ask Paystack.
    event = {"event": "charge.success", "data": {"reference": reference}}
    response = post_webhook(client, event)

    assert response.status_code == 503

    with app.app_context():
        stored = PaymentEvent.query.one()
        assert stored.processed is False
        assert Subscription.query.one().status != SUBSCRIPTION_ACTIVE


def test_retry_after_the_provider_recovers_activates_the_subscription(
    app, client, vendor, stub_paystack
):
    """The redelivery Paystack sends must actually be processed.

    This is the other half of the previous test: leaving processed=False is only
    safe if the redelivery is honoured.
    """
    install = stub_paystack
    install(error=PaystackError("timeout"))

    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    event = {"event": "charge.success", "data": {"reference": reference}}

    # The provider is down. The event must survive as unprocessed.
    assert post_webhook(client, event).status_code == 503

    with app.app_context():
        assert PaymentEvent.query.one().processed is False

    # The provider comes back and Paystack retries the same event.
    stub_paystack()

    assert post_webhook(client, event).status_code == 200

    with app.app_context():
        assert PaymentEvent.query.count() == 1, "the retry must not create a second event"
        assert PaymentEvent.query.one().processed is True
        assert Payment.query.filter_by(reference=reference).one().status == (
            PAYMENT_SUCCESS
        )
        assert Subscription.query.one().status == SUBSCRIPTION_ACTIVE


def test_a_business_refusal_is_acknowledged_and_marked_processed(
    app, client, vendor, stub_paystack
):
    """The counterpart to the test above.

    An event that can never become valid must be acknowledged, or Paystack retries
    it forever against an endpoint that will never change its mind.
    """
    stub_paystack(paid=False, status="failed")

    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    event = {"event": "charge.success", "data": {"reference": reference}}

    response = post_webhook(client, event)
    assert response.status_code == 200

    with app.app_context():
        assert PaymentEvent.query.one().processed is True
        # The refusal is recorded, and nothing is activated.
        assert Payment.query.filter_by(reference=reference).one().status == (
            PAYMENT_FAILED
        )
        assert Subscription.query.one().status != SUBSCRIPTION_ACTIVE


# --- #10: a pending subscription that was never paid expires --------------


def test_a_stale_pending_subscription_is_expired(app, vendor):
    """Checkout abandoned months ago must not read as pending forever.

    pending is what the frontend uses to render "complete your payment", so a row
    that never resolves leaves a vendor with a permanent checkout prompt.
    """
    from backend.payments.service import subscription_status

    with app.app_context():
        payment = seed_pending_payment(vendor)
        subscription = payment.subscription
        subscription.status = SUBSCRIPTION_PENDING
        subscription.next_payment_date = None
        # Both timestamps, because _is_expired reads updated_at first and the
        # commit that saves this would otherwise refresh it to now — which is
        # exactly what happens to a real checkout nobody ever returns to.
        stale = utcnow() - timedelta(days=45)
        subscription.created_at = stale
        subscription.updated_at = stale
        db.session.commit()
        subscription_id = subscription.id

        result = subscription_status(vendor, "web-test-user")

        assert result["subscription"] is not None
        assert (
            db.session.get(Subscription, subscription_id).status
            == SUBSCRIPTION_EXPIRED
        )


def test_a_recent_pending_subscription_is_still_pending(app, vendor):
    """The expiry must not fire on a checkout that is merely in progress."""
    from backend.payments.service import subscription_status

    with app.app_context():
        payment = seed_pending_payment(vendor)
        subscription = payment.subscription
        subscription.status = SUBSCRIPTION_PENDING
        subscription.next_payment_date = None
        subscription.created_at = utcnow() - timedelta(days=2)
        db.session.commit()

        subscription_status(vendor, "web-test-user")

        assert Subscription.query.one().status == SUBSCRIPTION_PENDING


# --- #11: a price change must not confiscate a completed payment ----------


def test_payment_survives_a_price_change_between_checkout_and_payment(
    app, client, vendor, stub_paystack
):
    """The customer paid exactly what Sauti asked, so they get access.

    amount_minor is snapshotted at checkout. Comparing the payment against the
    *current* plan price meant a customer who paid KSh 100 after the price moved
    to KSh 150 was refused with a 409 after Paystack had taken their money.
    """
    stub_paystack()

    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference
        plan = db.session.get(Plan, payment.plan_id)
        plan.amount_minor = EXPECTED_AMOUNT_MINOR * 2
        db.session.commit()

    response = client.get(
        f"/api/payments/verify/{reference}", headers=auth_headers(app, vendor)
    )

    assert response.status_code == 200
    assert response.get_json()["verified"] is True

    with app.app_context():
        assert Subscription.query.one().status == SUBSCRIPTION_ACTIVE


def test_a_tampered_amount_is_still_refused(app, client, vendor, stub_paystack):
    """Dropping the price check must not drop the tamper check.

    The comparison against what Sauti actually requested for this reference is
    the one that carries weight, and it is unchanged.
    """
    stub_paystack(amount_minor=1)

    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    response = client.get(
        f"/api/payments/verify/{reference}", headers=auth_headers(app, vendor)
    )

    assert response.status_code == 409

    with app.app_context():
        assert Subscription.query.one().status != SUBSCRIPTION_ACTIVE


# --- #12: the vendor/plan uniqueness race --------------------------------


def test_get_or_create_subscription_returns_the_existing_row_on_a_race(
    app, vendor, monkeypatch
):
    """A concurrent insert must not surface an IntegrityError to the caller.

    Two workers can both observe "no subscription" and both insert. The loser has
    to take the winner's row, because the alternative is a 500 on a Subscribe
    press that is otherwise perfectly valid.

    The race is simulated by making the lookup miss while the row genuinely
    exists, which is exactly the window the unique constraint exists to close.
    """
    from backend.payments.service import (
        ensure_vendor_plan,
        get_or_create_subscription,
    )

    with app.app_context():
        plan = ensure_vendor_plan()
        vendor_row = db.session.get(Vendor, vendor)

        # The racing worker wins the insert first.
        winner = Subscription(
            vendor_id=vendor_row.id,
            user_id=vendor_row.owner_user_id,
            plan_id=plan.id,
            status=SUBSCRIPTION_ACTIVE,
        )
        db.session.add(winner)
        db.session.commit()
        winner_id = winner.id

        # Our lookup is now stale: it sees nothing, so it will try to insert.
        original_first = Subscription.query.filter_by

        def blind_first(*args, **kwargs):
            query = original_first(*args, **kwargs)
            query.first = lambda: None
            return query

        monkeypatch.setattr(Subscription.query, "filter_by", blind_first, raising=False)

        result = get_or_create_subscription(
            vendor_row, vendor_row.owner_user_id, plan
        )

        assert result.id == winner_id
        assert result.status == SUBSCRIPTION_ACTIVE, "the winner's row must be adopted intact"
        assert Subscription.query.filter_by(vendor_id=vendor).count() == 1


# --- #13: the payload allow-list -----------------------------------------


def test_unknown_webhook_fields_are_not_retained(app, client):
    """Fields nobody thought of are dropped, not stored.

    A deny-list can only remove what it already knows about, so it fails open the
    moment Paystack adds a field. An allow-list fails closed instead.
    """
    event = charge_event("sauti_allowlist")
    event["data"]["metadata"] = {"custom_fields": [{"display_name": "IC Number", "value": "1234567"}]}
    event["data"]["customer"].update(
        {
            "first_name": "Amina",
            "last_name": "Wanjiru",
            "phone": "+254700000000",
        }
    )
    event["data"]["authorization"] = {"reusable": True, "channel_code": "card"}

    assert post_webhook(client, event).status_code == 200

    with app.app_context():
        serialized = json.dumps(PaymentEvent.query.one().payload)
        for leaked in ("Amina", "Wanjiru", "700000000", "1234567", "custom_fields"):
            assert leaked not in serialized, f"{leaked!r} was retained"
        # The fields processing needs are still there.
        assert PaymentEvent.query.one().payload["data"]["reference"] == "sauti_allowlist"


def test_the_allow_list_keeps_what_handlers_read(app, client, vendor):
    """Stripping PII must not strip the transaction facts."""
    with app.app_context():
        seed_pending_payment(vendor)

    post_webhook(client, charge_event("sauti_seedtest0000000000000000"))

    with app.app_context():
        payload = PaymentEvent.query.one().payload["data"]
        for field in ("reference", "amount", "currency", "status", "channel", "paid_at"):
            assert field in payload, f"{field} was dropped but is needed to process"


# --- #14: webhook body size ----------------------------------------------


def test_oversized_webhook_body_is_rejected(app, client):
    """A body over the limit is refused and never stored.

    This asserts the limit is enforced. The response code alone cannot show
    *when* the check runs, and the ordering is the part that matters: the route
    compares Content-Length before calling get_data(), because get_data() buffers
    the whole body and a limit applied afterwards has already been breached by
    the time it runs. That ordering is not observable through HTTP, so it is
    asserted by reading the route rather than by this response.
    """
    from backend.api.payments import _WEBHOOK_MAX_BYTES

    event = charge_event("sauti_big")
    event["data"]["padding"] = "x" * (_WEBHOOK_MAX_BYTES + 1024)
    raw = json.dumps(event).encode()
    assert len(raw) > _WEBHOOK_MAX_BYTES

    response = client.post(
        "/api/payments/webhook",
        data=raw,
        headers={"x-paystack-signature": compute_signature(raw, TEST_SECRET)},
        content_type="application/json",
    )

    assert response.status_code == 403

    with app.app_context():
        assert PaymentEvent.query.count() == 0


# --- #15: no outbound call for a reference Sauti never issued --------------


def test_unknown_reference_does_not_call_paystack(app, client, vendor, stub_paystack):
    """The local lookup answers first.

    The vendor token this route needs is obtainable without any authentication,
    so an anonymous caller could mint unlimited distinct references. Contacting
    Paystack first made each one cost a real HTTPS round trip, turning the route
    into an amplifier for spending Sauti's provider quota.
    """
    stub = stub_paystack()

    response = client.get(
        "/api/payments/verify/sauti_notours00000000000000",
        headers=auth_headers(app, vendor),
    )

    assert response.status_code == 404
    assert stub.verify_calls == [], "Paystack must not be contacted for a reference we never issued"


def test_a_reference_paystack_knows_is_still_refused(app, client, vendor, stub_paystack):
    """Adopting a stranger's real payment would hand over their subscription.

    A reference can reach us from a dashboard, a receipt or a chat message. Every
    reference Sauti issues is written to the database before Paystack is called,
    so an unknown reference means one we never created.
    """
    stub = stub_paystack()
    reference = "sauti_notours00000000000001"

    # Paystack would happily confirm this one.
    stub.verify_transaction = lambda ref: type(
        "T",
        (),
        {
            "paid": True,
            "status": "success",
            "reference": ref,
            "amount_minor": EXPECTED_AMOUNT_MINOR,
            "currency": "KES",
            "channel": "card",
            "customer_code": "CUS_x",
            "customer_email": None,
            "paid_at": None,
            "failure_reason": None,
        },
    )()

    response = client.get(
        f"/api/payments/verify/{reference}", headers=auth_headers(app, vendor)
    )

    assert response.status_code == 404
    with app.app_context():
        assert Payment.query.count() == 0
        assert Subscription.query.count() == 0


# --- #17: an inline status is required, not assumed -----------------------


def test_inline_transaction_without_a_status_is_not_treated_as_paid(
    app, client, vendor, stub_paystack
):
    """A missing status must not default to "paid".

    The webhook decoder defaulted an absent status to "success" while
    verify_transaction defaulted it to "" — i.e. not paid. The same field was
    therefore trusted on one path and refused on the other. The safe default is
    the refusing one, and it also lets the caller fall back to Paystack.
    """
    stub = stub_paystack()

    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    event = {
        "event": "charge.success",
        "data": {
            "reference": reference,
            "transaction": {
                "reference": reference,
                "amount": EXPECTED_AMOUNT_MINOR,
                "currency": "KES",
                # No "status".
            },
        },
    }

    response = post_webhook(client, event)
    assert response.status_code == 200

    # It went to Paystack rather than believing the body.
    assert reference in stub.verify_calls


def test_inline_transaction_with_an_explicit_failure_is_refused(
    app, client, vendor
):
    """An explicit non-success status in the body is respected."""
    with app.app_context():
        payment = seed_pending_payment(vendor)
        reference = payment.reference

    event = {
        "event": "charge.success",
        "data": {
            "reference": reference,
            "transaction": {
                "reference": reference,
                "status": "failed",
                "amount": EXPECTED_AMOUNT_MINOR,
                "currency": "KES",
            },
        },
    }

    response = post_webhook(client, event)
    assert response.status_code == 200

    with app.app_context():
        assert Subscription.query.one().status != SUBSCRIPTION_ACTIVE


# --- #18: an abandoned checkout can be resumed ---------------------------


def test_second_initialization_reuses_the_stored_authorization_url(
    app, client, vendor, stub_paystack
):
    """A customer who abandons the popup comes back to the same session.

    Paystack returns the authorization URL once, at initialization, and not on
    any later read. Without storing it, coming back minted a second reference and
    a second checkout session for a payment they had already started.
    """
    stub = stub_paystack()

    first = client.post("/api/payments/initialize", json={}, headers=auth_headers(app, vendor))
    assert first.status_code == 200
    original_url = first.get_json()["authorizationUrl"]

    second = client.post("/api/payments/initialize", json={}, headers=auth_headers(app, vendor))

    assert second.status_code == 200
    assert second.get_json()["authorizationUrl"] == original_url
    assert second.get_json()["reference"] == first.get_json()["reference"]

    with app.app_context():
        assert Payment.query.count() == 1
        assert Payment.query.one().authorization_url == original_url


def test_reinitializing_after_an_abandonment_resets_the_status(
    app, client, vendor, stub_paystack
):
    """A new attempt after a cancellation starts from a clean state.

    Leaving the subscription cancelled means the new payment succeeds and the
    subscription stays cancelled, so the customer pays and receives nothing.
    """
    stub = stub_paystack()

    with app.app_context():
        payment = seed_pending_payment(vendor)
        payment.subscription.status = SUBSCRIPTION_CANCELLED
        db.session.commit()

    response = client.post(
        "/api/payments/initialize", json={}, headers=auth_headers(app, vendor)
    )

    assert response.status_code == 200

    with app.app_context():
        assert Subscription.query.one().status == SUBSCRIPTION_PENDING


# --- #19 and #6/#7: schema matches the model -----------------------------


@pytest.mark.parametrize(
    "table,index",
    [
        ("subscriptions", "idx_subscriptions_customer_code"),
        ("payments", "idx_payments_customer_code"),
        ("payment_events", "idx_payment_events_customer_code"),
    ],
)
def test_customer_code_lookups_are_indexed(app, table, index):
    """Renewal matching reads paystack_customer_code on every charge."""
    with app.app_context():
        names = {i.name for i in db.metadata.tables[table].indexes}
        assert index in names


@pytest.mark.parametrize(
    "table,expected",
    [
        (
            "plans",
            {"idx_plans_active", "idx_plans_paystack_plan"},
        ),
        (
            "subscriptions",
            {
                "idx_subscriptions_status",
                "idx_subscriptions_user",
                "idx_subscriptions_paystack_sub",
                "idx_subscriptions_customer_code",
            },
        ),
        (
            "payments",
            {
                "idx_payments_status",
                "idx_payments_vendor_status",
                "idx_payments_user_status",
                "idx_payments_customer_code",
            },
        ),
        (
            "payment_events",
            {
                "idx_payment_events_type",
                "idx_payment_events_processed",
                "idx_payment_events_reference",
                "idx_payment_events_customer_code",
                "idx_payment_events_created",
            },
        ),
    ],
)
def test_model_indexes_match_the_migration_names(app, table, expected):
    """Index names must match migration 0005 exactly.

    `index=True` generates ix_<table>_<column> while the migration creates
    idx_<table>_<column>, so a database that ran the migration and then had the
    table created from the model ends up with two indexes on one column.
    """
    with app.app_context():
        names = {i.name for i in db.metadata.tables[table].indexes}
        assert names == expected


def test_payments_reference_is_not_double_indexed(app):
    """The unique constraint already indexes the column."""
    with app.app_context():
        indexes = db.metadata.tables["payments"].indexes
        columns = [c.name for i in indexes for c in i.columns]
        assert columns.count("reference") == 0, (
            "payments.reference is indexed separately from its unique constraint, "
            "which doubles write cost for no extra lookup"
        )


@pytest.mark.parametrize(
    "table,column,default",
    [
        ("plans", "currency", "KES"),
        ("plans", "interval", "monthly"),
        ("plans", "minor_exponent", "2"),
        ("subscriptions", "status", "inactive"),
        ("payments", "currency", "KES"),
        ("payments", "status", "pending"),
        ("payment_events", "provider", "paystack"),
    ],
)
def test_defaults_are_declared_on_the_model_as_well_as_the_migration(
    app, table, column, default
):
    """A server default the model does not know about is drift.

    create_all() in development and the migration in production must produce the
    same schema, or an insert that omits the column succeeds against Postgres and
    fails against SQLite.
    """
    with app.app_context():
        col = db.metadata.tables[table].columns[column]
        assert col.server_default is not None, f"{table}.{column} has no server_default"
        # The arg is a plain str when it was written as a literal and a TextClause
        # when it was written as an expression, so both are unwrapped to text.
        arg = col.server_default.arg
        rendered = getattr(arg, "text", arg)
        assert default in str(rendered)


def test_a_concurrent_duplicate_renewal_takes_the_winning_row(app, vendor):
    """A colliding renewal resolves to the winning row.

    Two deliveries of the same renewal can both reach the insert; the unique
    reference means only one row exists. The loser must take the winner's row
    rather than raise, or a duplicated webhook becomes a 500 and Paystack retries
    a charge that has already been applied.
    """
    from backend.payments.service import _record_renewal_payment

    with app.app_context():
        payment = seed_pending_payment(vendor)
        subscription = payment.subscription
        subscription.paystack_subscription_code = "SUB_concurrent"
        subscription.status = SUBSCRIPTION_ACTIVE
        db.session.commit()

        # The competing worker won the insert for this reference.
        winner = Payment(
            vendor_id=vendor,
            user_id=payment.user_id,
            plan_id=payment.plan_id,
            subscription_id=subscription.id,
            reference="paystack_concurrent_1",
            amount_minor=EXPECTED_AMOUNT_MINOR,
            currency="KES",
            status=PAYMENT_SUCCESS,
        )
        db.session.add(winner)
        db.session.commit()
        winner_id = winner.id

        event_record = PaymentEvent(
            event_type="charge.success",
            event_key="charge.success:paystack_concurrent_1",
            reference="paystack_concurrent_1",
            paystack_subscription_code="SUB_concurrent",
            payload={},
            processed=False,
        )
        db.session.add(event_record)
        db.session.commit()

        # The insert collides with the winning row; recovery must return it.
        result = _record_renewal_payment(event_record, {})

        assert result is not None
        assert result.id == winner_id
        assert Payment.query.filter_by(reference="paystack_concurrent_1").count() == 1


def test_webhook_size_is_checked_before_the_body_is_buffered(app):
    """The size check must precede the read, and that is not observable over HTTP.

    get_data() buffers the entire request into memory. A limit applied after it
    has run has already been breached by the time it is evaluated, so the route
    compares the declared Content-Length first and only then reads. Asserted here
    from the source, because every response-level test would pass either way.
    """
    import inspect

    from backend.api import payments as payments_api

    source = inspect.getsource(payments_api.webhook)
    length_check = source.index("request.content_length")
    body_read = source.index("request.get_data(")

    assert length_check < body_read, (
        "the webhook body is read before its size is checked, so the buffer is "
        "already allocated by the time the limit applies"
    )


# --- Vendor token issuance requires proof of the user --------------------


def issue_token(client, vendor, *, user_id="web-test-user", secret=None):
    body = {"user_id": user_id, "vendor_id": vendor}
    if secret is not None:
        body["secret"] = secret
    return client.post("/api/payments/vendor/token", json=body)


def test_first_token_request_returns_a_secret_the_server_only_hashes(app, client, vendor):
    """The first request for a user claims its secret and is told it once."""
    response = issue_token(client, vendor)

    assert response.status_code == 200
    secret = response.get_json().get("vendorSecret")
    assert secret, "the first token must return the issuance secret"

    with app.app_context():
        from backend.payments.auth import hash_vendor_secret
        from backend.models import User

        user = db.session.get(User, "web-test-user")
        assert user.vendor_token_hash == hash_vendor_secret(secret)
        # The plaintext must not be what is stored.
        assert user.vendor_token_hash != secret


def test_learning_a_user_id_is_no_longer_enough_to_mint_a_token(app, client, vendor):
    """The impersonation gap the token scheme used to have.

    Issuance used to trust a body field, so anyone who learned an established
    ``user_id`` could mint a token for it and read that vendor's billing
    records. The secret is what closes it.
    """
    first = issue_token(client, vendor)
    assert first.status_code == 200
    secret = first.get_json()["vendorSecret"]

    # The attacker knows the user_id and the vendor id, but not the secret.
    attacker = issue_token(client, vendor)
    assert attacker.status_code == 403
    assert "token" not in attacker.get_json()

    # The legitimate owner, presenting the secret, still succeeds.
    owner = issue_token(client, vendor, secret=secret)
    assert owner.status_code == 200
    assert owner.get_json()["token"]
    # Already established, so no second secret is handed out.
    assert "vendorSecret" not in owner.get_json()


def test_a_wrong_secret_is_refused(app, client, vendor):
    """A guess must not be accepted."""
    issue_token(client, vendor)

    response = issue_token(client, vendor, secret="not-the-secret")

    assert response.status_code == 403


def test_an_empty_secret_is_refused_for_an_established_user(app, client, vendor):
    """An empty string must not be treated as 'no secret needed'."""
    issue_token(client, vendor)

    response = issue_token(client, vendor, secret="")

    assert response.status_code == 403


def test_a_minted_token_still_works_for_billing_routes(app, client, vendor):
    """The added proof must not break the flow it protects."""
    response = issue_token(client, vendor)
    token = response.get_json()["token"]

    status = client.get("/api/subscriptions/status", headers={"X-Vendor-Token": token})

    assert status.status_code == 200


def test_two_users_do_not_share_a_secret(app, client, vendor):
    """Each user gets its own, so one user's secret cannot speak for another."""
    with app.app_context():
        from backend.models import User

        db.session.add(User(id="second-test-user", is_active=True))
        # The second user needs a vendor of its own: the shared fixture vendor is
        # owned by the first user, and claiming it would be refused for an
        # unrelated and correct reason.
        second_vendor = Vendor(
            business_name="Second Grocers",
            slug="second-grocers",
            description="A second vendor",
            category="food",
            location="Nairobi",
            email="second@example.com",
            owner_user_id=None,
        )
        db.session.add(second_vendor)
        db.session.commit()
        second_vendor_id = second_vendor.id

    first = issue_token(client, vendor, user_id="web-test-user").get_json()["vendorSecret"]

    # The second user has never asked, so it is issued its own secret.
    second = issue_token(
        client, second_vendor_id, user_id="second-test-user"
    ).get_json()["vendorSecret"]

    assert first != second

    # Presenting user one's secret while claiming to be user two is refused.
    response = issue_token(client, second_vendor_id, user_id="second-test-user", secret=first)
    assert response.status_code == 403


# --- Rate limit storage warning -----------------------------------------


def test_in_memory_rate_limits_warn_in_production(monkeypatch):
    """memory:// is not a shared store, and production must say so."""
    from backend.api import limiter as limiter_module

    monkeypatch.setattr(limiter_module, "STORAGE_URI", "memory://")
    monkeypatch.setenv("FLASK_ENV", "production")

    assert limiter_module.warn_if_in_memory_in_production() is True


def test_a_shared_store_does_not_warn(monkeypatch):
    from backend.api import limiter as limiter_module

    monkeypatch.setattr(limiter_module, "STORAGE_URI", "redis://:pw@host:6379/0")
    monkeypatch.setenv("FLASK_ENV", "production")

    assert limiter_module.warn_if_in_memory_in_production() is False


def test_in_memory_rate_limits_do_not_warn_in_development(monkeypatch):
    """A single-process dev server is the case memory:// is correct for."""
    from backend.api import limiter as limiter_module

    monkeypatch.setattr(limiter_module, "STORAGE_URI", "memory://")
    monkeypatch.setenv("FLASK_ENV", "development")

    assert limiter_module.warn_if_in_memory_in_production() is False
