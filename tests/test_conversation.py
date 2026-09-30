"""Tests for the conversation service."""
import pytest
from backend.services.conversation import ConversationService
from backend.models import Conversation, Message


class TestConversationService:
    """Tests for the ConversationService class."""

    def test_get_or_create_new(self, app):
        """Test creating a new conversation."""
        with app.app_context():
            service = ConversationService()
            conv = service.get_or_create()
            assert conv.id is not None
            assert conv.is_active is True

    def test_get_or_create_existing(self, app):
        """Test getting an existing conversation."""
        with app.app_context():
            service = ConversationService()
            conv1 = service.get_or_create("test-conv-123")
            conv2 = service.get_or_create("test-conv-123")
            assert conv1.id == conv2.id

    def test_add_message(self, app):
        """Test adding a message to a conversation."""
        with app.app_context():
            service = ConversationService()
            conv = service.get_or_create()
            msg = service.add_message(
                conversation_id=conv.id,
                role="user",
                content="Hello",
                language="en",
                intent="greeting",
                confidence=0.95
            )
            assert msg.id is not None
            assert msg.role == "user"
            assert msg.content == "Hello"

    def test_get_messages(self, app):
        """Test getting messages for a conversation."""
        with app.app_context():
            service = ConversationService()
            conv = service.get_or_create()
            service.add_message(conversation_id=conv.id, role="user", content="Hello")
            service.add_message(conversation_id=conv.id, role="assistant", content="Hi there")

            messages = service.get_messages(conv.id)
            assert len(messages) == 2

    def test_deactivate_conversation(self, app):
        """Test deactivating a conversation."""
        with app.app_context():
            service = ConversationService()
            conv = service.get_or_create()
            result = service.deactivate_conversation(conv.id)
            assert result is True
            assert conv.is_active is False
