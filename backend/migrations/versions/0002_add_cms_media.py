"""Add frontend CMS and media tables.

This migration is strictly additive. It creates new tables only and does not
alter, drop or rewrite any existing SautiPay data.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_cms_media"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def upgrade():
    op.create_table(
        "media_assets",
        *_timestamps(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("stored_filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("alt_text", sa.String(500), nullable=True),
        sa.Column("focal_x", sa.Float(), server_default="50.0", nullable=False),
        sa.Column("focal_y", sa.Float(), server_default="50.0", nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("uploaded_by_id", sa.String(), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_filename"),
    )
    op.create_index("idx_media_assets_public", "media_assets", ["is_public"])
    op.create_index("idx_media_assets_checksum", "media_assets", ["checksum"])

    op.create_table(
        "hero_slides",
        *_timestamps(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("eyebrow", sa.String(120), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("subtitle", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_id", sa.String(), nullable=True),
        sa.Column("mobile_image_id", sa.String(), nullable=True),
        sa.Column("focal_x", sa.Float(), server_default="50.0", nullable=False),
        sa.Column("focal_y", sa.Float(), server_default="50.0", nullable=False),
        sa.Column("cta_label", sa.String(120), nullable=True),
        sa.Column("cta_target", sa.String(500), nullable=True),
        sa.Column("display_duration", sa.Integer(), server_default="6000", nullable=False),
        sa.Column("transition_type", sa.String(40), server_default="fade", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mobile_image_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "categories",
        *_timestamps(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(120), nullable=True),
        sa.Column("image_id", sa.String(), nullable=True),
        sa.Column("accent", sa.String(24), server_default="#e86f3a", nullable=False),
        sa.Column("target", sa.String(500), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "stories",
        *_timestamps(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_id", sa.String(), nullable=True),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_type", sa.String(24), server_default="MANUAL", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_featured", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_visible", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_stories_published", "stories", ["is_published"])
    op.create_index("idx_stories_source_type", "stories", ["source_type"])

    op.create_table(
        "quick_actions",
        *_timestamps(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(120), nullable=True),
        sa.Column("action_type", sa.String(40), server_default="OPEN_PAGE", nullable=False),
        sa.Column("action_target", sa.String(500), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_quick_actions_published", "quick_actions", ["is_published"])
    op.create_index("idx_quick_actions_type", "quick_actions", ["action_type"])

    op.create_table(
        "featured_content",
        *_timestamps(),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_id", sa.String(), nullable=True),
        sa.Column("cta_label", sa.String(120), nullable=True),
        sa.Column("cta_target", sa.String(500), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("featured_content")
    op.drop_index("idx_quick_actions_type", table_name="quick_actions")
    op.drop_index("idx_quick_actions_published", table_name="quick_actions")
    op.drop_table("quick_actions")
    op.drop_index("idx_stories_source_type", table_name="stories")
    op.drop_index("idx_stories_published", table_name="stories")
    op.drop_table("stories")
    op.drop_table("categories")
    op.drop_table("hero_slides")
    op.drop_index("idx_media_assets_checksum", table_name="media_assets")
    op.drop_index("idx_media_assets_public", table_name="media_assets")
    op.drop_table("media_assets")
