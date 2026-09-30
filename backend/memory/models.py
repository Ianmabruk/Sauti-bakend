"""SQLAlchemy models for SAUTI long-term memory.

Conversation history (existing Conversation/Message tables) is deliberately
separate from this table. Only information that is clearly useful in future
sessions is stored here, and only when a caller asks for it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Index

from ..db import db
from ..models import generate_uuid, TimestampMixin

#: Memory categories. Kept small and explicit so recall can be filtered.
MEMORY_CATEGORIES = (
    "preference",      # language, tone, formatting preferences
    "fact",            # durable facts the user told us
    "project",         # active projects and their context
    "task",            # task history worth recalling
    "context",         # general long-term context
)

#: Categories that must never be auto-stored even if requested.
FORBIDDEN_CATEGORIES = ("sensitive", "credential", "secret", "financial_account")


class MemoryItem(db.Model, TimestampMixin):
    """A single retrievable long-term memory."""

    __tablename__ = "memory_items"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    user_id = db.Column(
        db.String, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    conversation_id = db.Column(
        db.String, db.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    category = db.Column(db.String(32), default="context", nullable=False)
    key = db.Column(db.String(160), nullable=True)
    content = db.Column(db.Text, nullable=False)
    language = db.Column(db.String(10), nullable=True)
    importance = db.Column(db.Float, default=0.5, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    access_count = db.Column(db.Integer, default=0, nullable=False)
    last_accessed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    extra_metadata = db.Column(db.JSON, nullable=True)

    __table_args__ = (
        Index("idx_memory_items_user", "user_id"),
        Index("idx_memory_items_category", "category"),
        Index("idx_memory_items_active", "is_active"),
        Index("idx_memory_items_key", "key"),
    )

    def touch(self) -> None:
        self.access_count += 1
        self.last_accessed_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "category": self.category,
            "key": self.key,
            "content": self.content,
            "language": self.language,
            "importance": self.importance,
            "is_active": self.is_active,
            "access_count": self.access_count,
            "last_accessed_at": self.last_accessed_at.isoformat()
            if self.last_accessed_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
