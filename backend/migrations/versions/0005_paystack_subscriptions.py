"""Add Paystack subscription tables.

Strictly additive: four new tables, no existing table altered, dropped or
rewritten. Existing Sauti users, vendors, marketplace orders and M-Pesa
transactions are untouched.

The tables are:

    plans             the sellable product and its Paystack plan code
    subscriptions     one row per (vendor, plan)
    payments          one row per checkout attempt, unique by reference
    payment_events    every webhook delivery, unique by (provider, event_key)

Two constraints carry real weight and are the reason this migration is worth
reading:

  * ``payments.reference`` is UNIQUE. Two checkouts can never share a
    reference, so a verification can never resolve to an ambiguous row.
  * ``payment_events`` is UNIQUE on (provider, event_key). Duplicate webhook
    suppression is enforced by the database rather than by a SELECT before an
    INSERT, which has a window in which two concurrent deliveries of the same
    event both see "not present" and both proceed to activate the same
    subscription twice.

Downgrade drops only these four tables. It is provided for completeness and is
not part of any deploy path; running it destroys subscription history.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_paystack_subscriptions"
down_revision = "0004_marketplace"
branch_labels = None
depends_on = None


def _ts():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def upgrade():
    op.create_table(
        "plans",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        # Smallest currency unit. 10000 == KSh 100.
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(8), server_default="KES", nullable=False),
        sa.Column("interval", sa.String(20), server_default="monthly", nullable=False),
        sa.Column("minor_exponent", sa.Integer(), server_default="2", nullable=False),
        # Nullable on purpose: the plan can exist locally before anyone opens the
        # Paystack Test dashboard and creates the real PLN_ code.
        sa.Column("paystack_plan_code", sa.String(80), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_plans_slug"),
    )
    op.create_index("idx_plans_active", "plans", ["is_active"])
    op.create_index("idx_plans_paystack_plan", "plans", ["paystack_plan_code"])

    op.create_table(
        "subscriptions",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("vendor_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("paystack_subscription_code", sa.String(120), nullable=True),
        sa.Column("paystack_customer_code", sa.String(120), nullable=True),
        sa.Column("paystack_email_token", sa.String(120), nullable=True),
        sa.Column("plan_code", sa.String(80), nullable=True),
        sa.Column(
            "status", sa.String(20), server_default="inactive", nullable=False
        ),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_payment_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_paystack_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        # One row per vendor per plan. A second Subscribe press updates this row
        # rather than creating a rival subscription.
        sa.UniqueConstraint(
            "vendor_id", "plan_id", name="uq_subscriptions_vendor_plan"
        ),
    )
    op.create_index("idx_subscriptions_status", "subscriptions", ["status"])
    op.create_index("idx_subscriptions_user", "subscriptions", ["user_id"])
    op.create_index(
        "idx_subscriptions_paystack_sub",
        "subscriptions",
        ["paystack_subscription_code"],
    )
    # Renewal charges routinely arrive without a subscription code, so matching
    # falls back to the customer code on every one of them.
    op.create_index(
        "idx_subscriptions_customer_code",
        "subscriptions",
        ["paystack_customer_code"],
    )

    op.create_table(
        "payments",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("vendor_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("plan_id", sa.String(), nullable=True),
        sa.Column("subscription_id", sa.String(), nullable=True),
        sa.Column("reference", sa.String(120), nullable=False),
        sa.Column("authorization_code", sa.String(120), nullable=True),
        sa.Column("access_code", sa.String(120), nullable=True),
        sa.Column("authorization_url", sa.Text(), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(8), server_default="KES", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("channel", sa.String(40), nullable=True),
        sa.Column("paystack_customer_code", sa.String(120), nullable=True),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        # The reference must identify exactly one payment. Without this, a
        # verification could resolve to more than one row.
        sa.UniqueConstraint("reference", name="uq_payments_reference"),
    )
    # No separate index on payments.reference. The unique constraint already
    # indexes the column, so a second plain index would double the write cost on
    # every insert while serving no lookup the unique index cannot answer.
    op.create_index("idx_payments_status", "payments", ["status"])
    op.create_index(
        "idx_payments_vendor_status", "payments", ["vendor_id", "status"]
    )
    op.create_index("idx_payments_user_status", "payments", ["user_id", "status"])
    op.create_index(
        "idx_payments_customer_code", "payments", ["paystack_customer_code"]
    )

    op.create_table(
        "payment_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column(
            "provider", sa.String(40), server_default="paystack", nullable=False
        ),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("event_key", sa.String(255), nullable=False),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("paystack_subscription_code", sa.String(120), nullable=True),
        sa.Column("paystack_customer_code", sa.String(120), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("processed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("processing_error", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # The replay guard. See the module docstring.
        sa.UniqueConstraint(
            "provider", "event_key", name="uq_payment_events_provider_key"
        ),
    )
    op.create_index("idx_payment_events_type", "payment_events", ["event_type"])
    op.create_index("idx_payment_events_processed", "payment_events", ["processed"])
    op.create_index("idx_payment_events_reference", "payment_events", ["reference"])
    op.create_index(
        "idx_payment_events_customer_code",
        "payment_events",
        ["paystack_customer_code"],
    )
    op.create_index("idx_payment_events_created", "payment_events", ["created_at"])


def downgrade():
    # Only the four tables this migration created. Nothing else is touched.
    for table in (
        "payment_events",
        "payments",
        "subscriptions",
        "plans",
    ):
        op.drop_table(table)