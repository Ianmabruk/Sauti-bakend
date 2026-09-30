"""Initial schema migration for SautiPay.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-22 00:00:00
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create all tables for SautiPay."""
    # Users
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone_number", sa.String(20), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("language_preference", sa.String(10), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Conversations
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Messages
    op.create_table(
        "messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("user", "assistant", "system", name="message_role"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("intent", sa.String(100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Languages
    op.create_table(
        "languages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("native_name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("has_verified_data", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.Index("idx_languages_active", "is_active"),
    )

    # Intents
    op.create_table(
        "intents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("training_phrases", sa.JSON(), nullable=True),
        sa.Column("required_entities", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("name"),
        sa.Index("idx_intents_active", "is_active"),
    )

    # Knowledge Sources
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(100), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("is_trusted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_verified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # Documents
    op.create_table(
        "documents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(100), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"], ["knowledge_sources.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Document Chunks
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.Index("idx_document_chunks_doc", "document_id"),
    )

    # Retrieval Records
    op.create_table(
        "retrieval_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("retrieved_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=True),
        sa.Column("similarity_scores", sa.JSON(), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("intent", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    """Drop all tables."""
    op.drop_table("retrieval_records")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("knowledge_sources")
    op.drop_table("intents")
    op.drop_table("languages")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("users")