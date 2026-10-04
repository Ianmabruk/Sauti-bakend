"""Paystack subscriptions for Sauti vendors.

Test-mode only by default. See :mod:`backend.payments.paystack` for the guard
that keeps a live key from being used unless live payments are explicitly
enabled, and :mod:`backend.payments.auth` for an honest account of how strong
vendor authentication is here.
"""
from .auth import (  # noqa: F401
    VENDOR_TOKEN_HEADER,
    VendorAuthError,
    VendorPrincipal,
    decode_vendor_token,
    issue_vendor_token,
    require_vendor,
)
from .models import (  # noqa: F401
    ENTITLED_SUBSCRIPTION_STATUSES,
    PAYMENT_ABANDONED,
    PAYMENT_FAILED,
    PAYMENT_PENDING,
    PAYMENT_STATUSES,
    PAYMENT_SUCCESS,
    SUBSCRIPTION_ACTIVE,
    SUBSCRIPTION_CANCELLED,
    SUBSCRIPTION_EXPIRED,
    SUBSCRIPTION_INACTIVE,
    SUBSCRIPTION_PAST_DUE,
    SUBSCRIPTION_PENDING,
    SUBSCRIPTION_STATUSES,
    SUBSCRIPTION_CURRENCY,
    Payment,
    PaymentEvent,
    Plan,
    Subscription,
    derive_event_key,
    major_to_minor,
    minor_to_major,
    utcnow,
)
from .paystack import (  # noqa: F401
    InitializedTransaction,
    PaystackClient,
    PaystackDisabledError,
    PaystackError,
    VerifiedTransaction,
    assert_paystack_mode,
    compute_signature,
    is_test_mode,
    paystack_mode,
    paystack_public_key,
    verify_signature,
)
from .service import (  # noqa: F401
    ACTIONABLE_EVENTS,
    REFERENCE_PREFIX,
    VENDOR_PLAN_SLUG,
    PaymentServiceError,
    RetryableWebhookError,
    ensure_vendor_plan,
    initialize_payment,
    new_reference,
    process_event,
    record_event,
    resolve_vendor_identity,
    subscription_status,
    verify_payment,
)