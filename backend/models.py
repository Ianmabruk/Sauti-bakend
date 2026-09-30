"""SQLAlchemy models for SautiPay."""
import uuid
from datetime import datetime, timezone

from .db import db


def generate_uuid() -> str:
    """Generate a UUID string."""
    return str(uuid.uuid4())


class TimestampMixin:
    """Mixin for created_at/updated_at fields."""

    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class User(db.Model, TimestampMixin):
    """User account."""

    __tablename__ = "users"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    phone_number = db.Column(db.String(20), unique=True, nullable=True)
    name = db.Column(db.String(255), nullable=True)
    language_preference = db.Column(db.String(10), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    conversations = db.relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    media_assets = db.relationship("MediaAsset", back_populates="uploaded_by")


class Conversation(db.Model, TimestampMixin):
    """Conversation session."""

    __tablename__ = "conversations"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    user_id = db.Column(db.String, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    language = db.Column(db.String(10), nullable=True)
    title = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    user = db.relationship("User", back_populates="conversations")
    messages = db.relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(db.Model, TimestampMixin):
    """Individual message in a conversation."""

    __tablename__ = "messages"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    conversation_id = db.Column(
        db.String, db.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role = db.Column(db.Enum("user", "assistant", "system", name="message_role"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    language = db.Column(db.String(10), nullable=True)
    intent = db.Column(db.String(100), nullable=True)
    confidence = db.Column(db.Float, nullable=True)
    sources = db.Column(db.JSON, nullable=True)

    conversation = db.relationship("Conversation", back_populates="messages")


class Language(db.Model, TimestampMixin):
    """Supported language metadata."""

    __tablename__ = "languages"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    code = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    native_name = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    has_verified_data = db.Column(db.Boolean, default=False, nullable=False)

    __table_args__ = (
        db.Index("idx_languages_active", "is_active"),
    )


class Intent(db.Model, TimestampMixin):
    """Intent metadata."""

    __tablename__ = "intents"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    training_phrases = db.Column(db.JSON, nullable=True)
    required_entities = db.Column(db.JSON, nullable=True)

    __table_args__ = (
        db.Index("idx_intents_active", "is_active"),
    )


class KnowledgeSource(db.Model, TimestampMixin):
    """Trusted source of information."""

    __tablename__ = "knowledge_sources"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    name = db.Column(db.String(255), nullable=False)
    domain = db.Column(db.String(100), nullable=True)
    url = db.Column(db.Text, nullable=True)
    is_trusted = db.Column(db.Boolean, default=False, nullable=False)
    last_verified = db.Column(db.DateTime(timezone=True), nullable=True)
    extra_metadata = db.Column(db.JSON, nullable=True)


class Document(db.Model, TimestampMixin):
    """Ingested document."""

    __tablename__ = "documents"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    source_id = db.Column(
        db.String, db.ForeignKey("knowledge_sources.id", ondelete="SET NULL"), nullable=True
    )
    title = db.Column(db.String(500), nullable=False)
    content = db.Column(db.Text, nullable=False)
    domain = db.Column(db.String(100), nullable=True)
    language = db.Column(db.String(10), nullable=True)
    extra_metadata = db.Column(db.JSON, nullable=True)

    chunks = db.relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(db.Model, TimestampMixin):
    """Chunk of a document for retrieval."""

    __tablename__ = "document_chunks"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    document_id = db.Column(
        db.String, db.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index = db.Column(db.Integer, nullable=False)
    content = db.Column(db.Text, nullable=False)
    embedding = db.Column(db.JSON, nullable=True)
    extra_metadata = db.Column(db.JSON, nullable=True)

    document = db.relationship("Document", back_populates="chunks")

    __table_args__ = (
        db.Index("idx_document_chunks_doc", "document_id"),
    )


class RetrievalRecord(db.Model, TimestampMixin):
    """Record of a retrieval operation for attribution."""

    __tablename__ = "retrieval_records"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    conversation_id = db.Column(
        db.String, db.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    message_id = db.Column(
        db.String, db.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    query = db.Column(db.Text, nullable=False)
    retrieved_chunk_ids = db.Column(db.JSON, nullable=False)
    source_ids = db.Column(db.JSON, nullable=True)
    similarity_scores = db.Column(db.JSON, nullable=True)
    language = db.Column(db.String(10), nullable=True)
    intent = db.Column(db.String(100), nullable=True)


class MediaAsset(db.Model, TimestampMixin):
    """Uploaded visual asset used by frontend content."""

    __tablename__ = "media_assets"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False, unique=True)
    mime_type = db.Column(db.String(100), nullable=False)
    file_size = db.Column(db.BigInteger, nullable=False)
    storage_path = db.Column(db.String(500), nullable=False)
    checksum = db.Column(db.String(64), nullable=True)
    alt_text = db.Column(db.String(500), nullable=True)
    focal_x = db.Column(db.Float, default=50.0, nullable=False)
    focal_y = db.Column(db.Float, default=50.0, nullable=False)
    is_public = db.Column(db.Boolean, default=True, nullable=False)
    uploaded_by_id = db.Column(db.String, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    extra_metadata = db.Column(db.JSON, nullable=True)

    uploaded_by = db.relationship("User", back_populates="media_assets")

    __table_args__ = (
        db.Index("idx_media_assets_public", "is_public"),
        db.Index("idx_media_assets_checksum", "checksum"),
    )


class HeroSlide(db.Model, TimestampMixin):
    """Configurable homepage hero slide."""

    __tablename__ = "hero_slides"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    eyebrow = db.Column(db.String(120), nullable=True)
    title = db.Column(db.String(255), nullable=False)
    subtitle = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    image_id = db.Column(db.String, db.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    mobile_image_id = db.Column(db.String, db.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    focal_x = db.Column(db.Float, default=50.0, nullable=False)
    focal_y = db.Column(db.Float, default=50.0, nullable=False)
    cta_label = db.Column(db.String(120), nullable=True)
    cta_target = db.Column(db.String(500), nullable=True)
    display_duration = db.Column(db.Integer, default=6000, nullable=False)
    transition_type = db.Column(db.String(40), default="fade", nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=True)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    image = db.relationship("MediaAsset", foreign_keys=[image_id], lazy="select")
    mobile_image = db.relationship("MediaAsset", foreign_keys=[mobile_image_id], lazy="select")


class Category(db.Model, TimestampMixin):
    """Configurable category/service card."""

    __tablename__ = "categories"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    icon = db.Column(db.String(120), nullable=True)
    image_id = db.Column(db.String, db.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    accent = db.Column(db.String(24), default="#e86f3a", nullable=False)
    target = db.Column(db.String(500), nullable=True)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=True)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    image = db.relationship("MediaAsset", foreign_keys=[image_id], lazy="select")


class Story(db.Model, TimestampMixin):
    """Curated or future live-data story."""

    __tablename__ = "stories"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    image_id = db.Column(db.String, db.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    category = db.Column(db.String(120), nullable=True)
    source = db.Column(db.String(255), nullable=True)
    source_url = db.Column(db.Text, nullable=True)
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    source_type = db.Column(db.String(24), default="MANUAL", nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=True)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_featured = db.Column(db.Boolean, default=False, nullable=False)
    is_visible = db.Column(db.Boolean, default=False, nullable=False)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    image = db.relationship("MediaAsset", foreign_keys=[image_id], lazy="select")

    __table_args__ = (
        db.Index("idx_stories_published", "is_published"),
        db.Index("idx_stories_source_type", "source_type"),
    )


class QuickAction(db.Model, TimestampMixin):
    """Configurable homepage shortcut."""

    __tablename__ = "quick_actions"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)
    icon = db.Column(db.String(120), nullable=True)
    action_type = db.Column(db.String(40), default="OPEN_PAGE", nullable=False)
    action_target = db.Column(db.String(500), nullable=True)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=True)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    __table_args__ = (
        db.Index("idx_quick_actions_published", "is_published"),
        db.Index("idx_quick_actions_type", "action_type"),
    )


class FeaturedContent(db.Model, TimestampMixin):
    """Configurable large editorial/service card."""

    __tablename__ = "featured_content"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    image_id = db.Column(db.String, db.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)
    cta_label = db.Column(db.String(120), nullable=True)
    cta_target = db.Column(db.String(500), nullable=True)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=True)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    image = db.relationship("MediaAsset", foreign_keys=[image_id], lazy="select")

