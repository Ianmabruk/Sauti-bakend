"""Add SAUTI long-term memory tables.

Additive only: creates new tables and never alters existing data.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_sauti_memory"
down_revision = "0002_cms_media"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_items",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("category", sa.String(32), server_default="context", nullable=False),
        sa.Column("key", sa.String(160), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("importance", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("access_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_memory_items_user", "memory_items", ["user_id"])
    op.create_index("idx_memory_items_category", "memory_items", ["category"])
    op.create_index("idx_memory_items_active", "memory_items", ["is_active"])
    op.create_index("idx_memory_items_key", "memory_items", ["key"])


def downgrade():
    op.drop_index("idx_memory_items_key", table_name="memory_items")
    op.drop_index("idx_memory_items_active", table_name="memory_items")
    op.drop_index("idx_memory_items_category", table_name="memory_items")
    op.drop_index("idx_memory_items_user", table_name="memory_items")
    op.drop_table("memory_items")
