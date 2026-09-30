"""Tests for database models and connection."""
import pytest
from backend.db import db
from backend.models import (
    User, Conversation, Message, Language, Intent,
    KnowledgeSource, Document, DocumentChunk, RetrievalRecord
)


class TestUserModel:
    """Tests for the User model."""

    def test_create_user(self, app):
        """Test creating a user."""
        with app.app_context():
            user = User(phone_number="+254700000000", name="Test User")
            db.session.add(user)
            db.session.commit()
            assert user.id is not None
            assert user.is_active is True

    def test_user_has_conversations(self, app):
        """Test that user has conversations relationship."""
        with app.app_context():
            user = User(phone_number="+254700000000", name="Test User")
            db.session.add(user)
            db.session.commit()

            conv = Conversation(user_id=user.id, language="en")
            db.session.add(conv)
            db.session.commit()

            assert len(user.conversations) == 1


class TestConversationModel:
    """Tests for the Conversation model."""

    def test_create_conversation(self, app):
        """Test creating a conversation."""
        with app.app_context():
            conv = Conversation(language="en")
            db.session.add(conv)
            db.session.commit()
            assert conv.id is not None
            assert conv.is_active is True

    def test_conversation_has_messages(self, app):
        """Test that conversation has messages relationship."""
        with app.app_context():
            conv = Conversation(language="en")
            db.session.add(conv)
            db.session.commit()

            msg = Message(conversation_id=conv.id, role="user", content="Hello")
            db.session.add(msg)
            db.session.commit()

            assert len(conv.messages) == 1


class TestMessageModel:
    """Tests for the Message model."""

    def test_create_message(self, app):
        """Test creating a message."""
        with app.app_context():
            conv = Conversation(language="en")
            db.session.add(conv)
            db.session.commit()

            msg = Message(
                conversation_id=conv.id,
                role="user",
                content="Hello",
                language="en",
                intent="greeting",
                confidence=0.95
            )
            db.session.add(msg)
            db.session.commit()

            assert msg.id is not None
            assert msg.role == "user"
            assert msg.content == "Hello"


class TestLanguageModel:
    """Tests for the Language model."""

    def test_create_language(self, app):
        """Test creating a language."""
        with app.app_context():
            lang = Language(code="test", name="Test", native_name="Test")
            db.session.add(lang)
            db.session.commit()
            assert lang.id is not None
            assert lang.code == "test"

    def test_language_code_is_unique(self, app):
        """Test that language code is unique."""
        with app.app_context():
            lang1 = Language(code="test", name="Test1", native_name="Test1")
            db.session.add(lang1)
            db.session.commit()

            lang2 = Language(code="test", name="Test2", native_name="Test2")
            db.session.add(lang2)
            with pytest.raises(Exception):
                db.session.commit()


class TestIntentModel:
    """Tests for the Intent model."""

    def test_create_intent(self, app):
        """Test creating an intent."""
        with app.app_context():
            intent = Intent(name="test_intent", description="Test intent")
            db.session.add(intent)
            db.session.commit()
            assert intent.id is not None
            assert intent.name == "test_intent"

    def test_intent_name_is_unique(self, app):
        """Test that intent name is unique."""
        with app.app_context():
            intent1 = Intent(name="test_intent", description="Test1")
            db.session.add(intent1)
            db.session.commit()

            intent2 = Intent(name="test_intent", description="Test2")
            db.session.add(intent2)
            with pytest.raises(Exception):
                db.session.commit()


class TestKnowledgeModels:
    """Tests for knowledge-related models."""

    def test_create_knowledge_source(self, app):
        """Test creating a knowledge source."""
        with app.app_context():
            source = KnowledgeSource(name="Test Source", domain="test", is_trusted=True)
            db.session.add(source)
            db.session.commit()
            assert source.id is not None

    def test_create_document_with_source(self, app):
        """Test creating a document with a source."""
        with app.app_context():
            source = KnowledgeSource(name="Test Source", domain="test")
            db.session.add(source)
            db.session.commit()

            doc = Document(
                source_id=source.id,
                title="Test Document",
                content="Test content",
                domain="test"
            )
            db.session.add(doc)
            db.session.commit()

            assert doc.id is not None
            assert doc.source_id == source.id

    def test_create_document_chunk(self, app):
        """Test creating a document chunk."""
        with app.app_context():
            doc = Document(title="Test", content="Test content")
            db.session.add(doc)
            db.session.commit()

            chunk = DocumentChunk(
                document_id=doc.id,
                chunk_index=0,
                content="Test chunk"
            )
            db.session.add(chunk)
            db.session.commit()

            assert chunk.id is not None
            assert chunk.document_id == doc.id

    def test_retrieval_record(self, app):
        """Test creating a retrieval record."""
        with app.app_context():
            conv = Conversation(language="en")
            db.session.add(conv)
            db.session.commit()

            msg = Message(conversation_id=conv.id, role="user", content="Test")
            db.session.add(msg)
            db.session.commit()

            record = RetrievalRecord(
                conversation_id=conv.id,
                message_id=msg.id,
                query="Test query",
                retrieved_chunk_ids=["chunk1", "chunk2"],
                source_ids=["source1"],
                similarity_scores=[0.95, 0.85]
            )
            db.session.add(record)
            db.session.commit()

            assert record.id is not None
            assert record.query == "Test query"
