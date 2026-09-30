"""Add Sauti marketplace tables.

Strictly additive: creates new tables only. Existing SautiPay and SAUTI data is
not altered, dropped or rewritten.

On PostgreSQL the `embedding` JSON column can later be replaced by a pgvector
column. Keeping it JSON here means the same schema and the same application code
work on SQLite today, so the pgvector upgrade is an additive change rather than
a rewrite.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_marketplace"
down_revision = "0003_sauti_memory"
branch_labels = None
depends_on = None


def _ts():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def upgrade():
    op.create_table(
        "marketplace_categories",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(60), nullable=True),
        sa.Column("accent", sa.String(24), server_default="#8B5E3C", nullable=False),
        sa.Column("image_url", sa.String(600), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "vendors",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("business_name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("subcategories", sa.JSON(), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("logo_url", sa.String(600), nullable=True),
        sa.Column("cover_url", sa.String(600), nullable=True),
        sa.Column("verification_status", sa.String(20), server_default="PENDING", nullable=False),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("opening_hours", sa.JSON(), nullable=True),
        sa.Column("service_areas", sa.JSON(), nullable=True),
        sa.Column("delivery_available", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("accepts_mpesa", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("idx_vendors_category", "vendors", ["category"])
    op.create_index("idx_vendors_location", "vendors", ["location"])
    op.create_index("idx_vendors_verification", "vendors", ["verification_status"])

    op.create_table(
        "products",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("vendor_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("slug", sa.String(320), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("subcategory", sa.String(120), nullable=True),
        sa.Column("price_minor", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(8), server_default="KES", nullable=False),
        sa.Column("price_label", sa.String(120), nullable=True),
        sa.Column("availability", sa.String(24), server_default="IN_STOCK", nullable=False),
        sa.Column("quantity_available", sa.Integer(), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("images", sa.JSON(), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("specifications", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(80), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_featured", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("view_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("inquiry_count", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_products_vendor", "products", ["vendor_id"])
    op.create_index("idx_products_category", "products", ["category"])
    op.create_index("idx_products_availability", "products", ["availability"])
    op.create_index("idx_products_price", "products", ["price_minor"])
    op.create_index("idx_products_active", "products", ["is_active"])

    op.create_table(
        "news_articles",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("slug", sa.String(420), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(600), nullable=True),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("source_type", sa.String(40), server_default="editorial", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_featured", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("idx_news_published", "news_articles", ["is_published"])
    op.create_index("idx_news_category", "news_articles", ["category"])

    op.create_table(
        "vendor_verifications",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("vendor_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("document_reference", sa.String(255), nullable=True),
        sa.Column("reviewed_by", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "saved_items",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("item_type", sa.String(24), nullable=False),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_saved_user", "saved_items", ["user_id"])
    op.create_index("idx_saved_item", "saved_items", ["item_type", "item_id"])

    op.create_table(
        "search_events",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("normalised_query", sa.String(400), nullable=True),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("result_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("had_results", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("source", sa.String(40), server_default="search_bar", nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "reviews",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("vendor_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "orders",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("reference", sa.String(40), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("vendor_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(24), server_default="PENDING", nullable=False),
        sa.Column("total_minor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(8), server_default="KES", nullable=False),
        sa.Column("delivery_details", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(32), server_default="user", nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
    )

    op.create_table(
        "order_items",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("order_id", sa.String(), nullable=False),
        sa.Column("product_id", sa.String(), nullable=True),
        sa.Column("product_name", sa.String(300), nullable=False),
        sa.Column("unit_price_minor", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "transactions",
        *_ts(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("order_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(40), server_default="mpesa", nullable=False),
        sa.Column("provider_reference", sa.String(120), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(8), server_default="KES", nullable=False),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("status", sa.String(24), server_default="INITIATED", nullable=False),
        sa.Column("raw_response", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    for table in (
        "transactions", "order_items", "orders", "reviews", "search_events",
        "saved_items", "vendor_verifications", "news_articles", "products",
        "vendors", "marketplace_categories",
    ):
        op.drop_table(table)
