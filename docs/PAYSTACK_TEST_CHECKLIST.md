# Paystack test-mode checklist

Vendor subscriptions, KSh 100 / month, KES, Paystack **Test Mode** only.

Nothing here touches real money. Do not proceed past section 2 until every
scenario in section 1 passes.

---

## 0. Why there is a checklist at all

Two failures in this system are silent and unrecoverable:

- **A live key on a test deployment charges real people.** The code refuses this
  (`PAYSTACK_ALLOW_LIVE` must be explicitly set, and
  `backend/payments/paystack.py` rejects `sk_live_` without it), but a key
  pasted into the Render dashboard is outside the code's reach.
- **A secret key in the frontend publishes it.** `NEXT_PUBLIC_*` variables are
  inlined into the client bundle at build time. One `NEXT_PUBLIC_PAYSTACK_SECRET_KEY`
  hands the secret to every visitor. `lib/payments.ts` throws if that variable
  starts with `sk_`, but the check runs in the browser — by then the key is
  already published. Section 4 must be run against the *deployed* bundle.

---

## 1. Backend tests

Automated. No keys needed.

```bash
python -m pytest tests/test_payments.py -q     # 72 tests
python -m pytest -q                            # 877 tests, whole suite
```

Coverage is mapped to the scenarios below; the test name is given so a failure
traces back to a risk.

| # | Scenario | Test |
|---|---|---|
| 1 | Successful test payment | `test_verify_activates_a_genuine_payment`, `test_webhook_accepts_a_correctly_signed_event` |
| 2 | Failed payment | `test_verify_does_not_activate_on_an_unpaid_transaction`, `test_charge_success_whose_status_is_not_success_is_refused` |
| 3 | Invalid transaction reference | `test_verify_rejects_an_unknown_reference`, `test_verify_rejects_a_malformed_reference` |
| 4 | Incorrect amount | `test_verify_refuses_an_amount_mismatch`, `test_charge_success_with_a_wrong_amount_does_not_activate` |
| 5 | Incorrect currency | `test_verify_refuses_a_currency_mismatch`, `test_charge_success_with_a_wrong_currency_does_not_activate` |
| 6 | Invalid webhook signature | `test_webhook_rejects_an_invalid_signature`, `test_webhook_rejects_a_signature_from_the_wrong_secret` |
| 7 | Missing webhook signature | `test_webhook_rejects_a_missing_signature` |
| 8 | Duplicate webhook | `test_duplicate_webhook_activates_only_once`, `test_duplicate_detection_survives_concurrent_delivery` |
| 9 | Unknown webhook event | `test_unhandled_event_is_recorded_and_acknowledged`, `test_webhook_with_a_non_object_body_is_acknowledged` |
| 10 | Repeated payment initialization | `test_repeated_initialization_reuses_one_reference`, `test_payment_initialization_is_rate_limited` |
| 11 | Unauthorized subscription request | `test_initialize_requires_authentication`, `test_verify_requires_authentication`, `test_status_requires_authentication` |
| 12 | Expired / inactive subscription | `test_status_reports_inactive_for_a_never_paying_vendor`, `test_stale_pending_subscription_becomes_expired` |
| 13 | Backend unavailable | `test_initialize_requires_authentication` (network-loss path in `ApiError`) |
| 14 | Paystack API unavailable | `test_paystack_unavailable_during_initialize` |
| 17 | Manipulate amount from browser | `test_client_cannot_manipulate_the_amount` |
| 18 | Activate without payment | `test_verify_never_activates_without_a_payment` |
| 19 | Secret key never reaches browser | `test_no_payment_response_body_contains_a_secret_key`, `test_config_endpoint_only_exposes_the_public_key` |

## 2. Manual tests against Paystack Test Mode

Needs real test keys. A checkout is refused with 503 until `PAYSTACK_PLAN_CODE`
is set, so this section cannot pass before section 0.

Test cards:

| Scenario | Card | Expected |
|---|---|---|
| Success | `4084 0840 8408 4081`, any future expiry, any CVV | Payment succeeds |
| Declined | `4000 0000 0000 9995` | Payment fails, no subscription |
| Wrong CVV | success card, CVV `000` | Payment fails |
| Insufficient funds | `4084 0840 8408 4081` + OTP `0000` in sandbox | Payment fails |

| # | Scenario | How | Expected |
|---|---|---|---|
| 1 | Successful test payment | Subscribe, pay with the success card | "Payment successful." then "Subscription activated." |
| 2 | Failed payment | Subscribe, pay with the declined card | "Payment failed." Status stays `inactive` or `pending` |
| 15 | Refresh the callback page | Reload `/payment/callback` five times | Same result each time. The paid window must not move forward |
| 16 | Open callback without a reference | Visit `/payment/callback` bare | "Payment could not be verified" |
| — | Duplicate webhook from Paystack | Paystack dashboard → Transactions → "Resend" on the event | HTTP 200, `{"message": "duplicate ignored"}`. Check `payment_events` has one row |
| — | Wrong amount | Edit the amount in devtools, resubmit | Request still charges KSh 100 |
| — | Concurrent duplicate | Send the signed event twice in parallel | One row in `payment_events`, one activation |

Verify in the database:

```sql
SELECT status, next_payment_date FROM subscriptions;
SELECT reference, amount_minor, currency, status FROM payments;
SELECT event_type, processed, created_at FROM payment_events ORDER BY created_at;
```

## 3. Browser security test

Run against the **deployed** build, not local dev.

```bash
npm run build
grep -r --include='*.js' -oE 'sk_(test|live)_[A-Za-z0-9]*' .next/static/
```

Must print nothing. Confirm `pk_test_` **does** appear.

Then in the browser:

- DevTools → Application → Local Storage / Session Storage / Cookies: no `sk_`
- Network tab: no request or response body contains `sk_`
- Console: no `sk_`

| 19 | Secret key never reaches the browser | DevTools → Sources → search `sk_` | Only the `"sk_"` guard prefix, never a key |
| 20 | Secret key never in the bundle | The `grep` above | No output |

## 4. Paystack dashboard setup

1. Toggle **Test Mode** ON. Read both keys from
   **Settings → API Keys**. Verify they start `sk_test_` / `pk_test_`.
2. Create the plan (Settings → Plans → Create Plan):

   | Field | Value |
   |---|---|
   | Name | Sauti Vendor Subscription |
   | Amount | `10000` (smallest unit) |
   | Currency | KES |
   | Interval | Monthly |

   Or by API:

   ```bash
   curl https://api.paystack.co/plan \
     -H "Authorization: Bearer $PAYSTACK_SECRET_KEY" \
     -d '{"name":"Sauti Vendor Subscription","amount":10000,"currency":"KES","interval":"monthly"}'
   ```

3. Set the returned `PLN_...` as `PAYSTACK_PLAN_CODE` on the **backend only**.

   This can be set before or after the first deploy. If the plan row was already
   created with no code, the next checkout fills it in from configuration. If the
   row already holds a *different* code, that code is kept and the mismatch is
   logged as a warning rather than overwritten — moving new subscribers to a
   different Paystack plan is a data change, not a config edit, so it is left for
   an operator to make deliberately.

4. Register the webhook:

   | Field | Value |
   |---|---|
   | URL | `https://<backend-domain>/api/payments/webhook` |
   | Events | `charge.success`, `subscription.create`, `invoice.create`, `invoice.payment_failed`, `subscription.disable`, `subscription.not_renew` |

   Never `localhost`, `127.0.0.1`, or the frontend URL.

## 5. Recurring renewals

Because checkout sends a plan code, Paystack charges the subscription
automatically each interval and issues **its own reference** for every renewal —
one Sauti never generated. Those charges are matched on the subscription code and,
where Paystack omits it, the customer code, and recorded against the plan price so
the amount check still has something to compare against.

To confirm this rather than assume it, after the first successful checkout:

1. Wait for, or trigger, the next billing date. In Test Mode a renewal can be
   forced from the dashboard by sending a test invoice to the subscription.
2. Check that a second `payments` row appeared with a `paystack...` reference and
   status `success`, and that the subscription's `next_payment_date` moved
   forward.
3. Confirm the original payment's window did not jump by more than one period —
   a duplicate delivery must not grant a second month.

## 6. Before live money

Not production-ready until all of these are done:

- [ ] **Real authentication.** Sauti still has no password, so there is no way to
      prove who is asking — only that they hold a secret. Token *issuance* now
      requires a per-user secret (`users.vendor_token_hash`, returned once to the
      browser), so learning a `user_id` is no longer enough to mint tokens for it.
      That is a real narrowing of the original gap, not a closure: anyone who
      reads the secret out of a browser's storage can still act as that user, and
      the secret cannot be rotated by proving identity, because proving identity
      is the missing piece. Note also that `app/subscription/page.tsx` stores the
      secret in `localStorage`, so an XSS bug becomes a billing-credential
      disclosure. Closing this properly means real accounts.
- [ ] `PAYSTACK_ALLOW_LIVE=true` and live keys, together, never before.
- [ ] A shared rate-limit store. `RATE_LIMIT_STORAGE=memory://` means each of the
      two gunicorn workers enforces its own budget, so the effective limit is 2×
      configured, and a restart clears every counter. The app logs a warning at
      boot when this is left in place with `FLASK_ENV=production`; treat that
      warning as a blocker rather than as noise.
- [ ] Webhook URL confirmed against the deployed domain.
- [ ] All of section 2 passing against the deployed backend.
- [ ] `alembic current` reporting `0006_schema_fixes` on the production database.
      The deploy runs `alembic upgrade head` before gunicorn starts;
      `create_all()` is skipped in production because it cannot add a column to a
      table that already exists, so a database that somehow missed the migration
      will boot healthy against a schema the code does not match.
- [ ] **Migration 0006 may refuse to run.** It adds `UNIQUE` on
      `users.phone_number` to match a constraint the models have always claimed.
      If real duplicates exist it aborts and names them rather than deleting or
      nulling anyone's identity column. Check first:
      ```sql
      SELECT phone_number, COUNT(*) FROM users
      WHERE phone_number IS NOT NULL AND phone_number <> ''
      GROUP BY phone_number HAVING COUNT(*) > 1;
      ```
      Resolve those rows, then deploy. The migration also moves the `intents`
      primary key from `name` to `id`, correcting a mistake in `0001_initial` that
      meant development and production had different schemas for that table.