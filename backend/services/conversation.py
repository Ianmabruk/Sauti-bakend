"""Conversation management service for SautiPay."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..db import db
from ..models import Conversation, Message, User

logger = logging.getLogger(__name__)


class ConversationService:
    """Manages conversations and messages."""

    def get_or_create(self, conversation_id: Optional[str] = None) -> Conversation:
        """Get an existing conversation or create a new one.

        Args:
            conversation_id: Optional conversation ID.

        Returns:
            A Conversation instance.
        """
        if conversation_id:
            conversation = Conversation.query.filter_by(id=conversation_id, is_active=True).first()
            if conversation:
                return conversation

        # Create new conversation
        conversation = Conversation(
            id=conversation_id,
            language=None,
            title=None,
            is_active=True,
        )
        db.session.add(conversation)
        db.session.commit()
        return conversation

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        language: Optional[str] = None,
        intent: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> Message:
        """Add a message to a conversation.

        Args:
            conversation_id: The conversation ID.
            role: Message role (user, assistant, system).
            content: Message content.
            language: Optional language code.
            intent: Optional detected intent.
            confidence: Optional confidence score.

        Returns:
            The created Message instance.
        """
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            language=language,
            intent=intent,
            confidence=confidence,
        )
        db.session.add(message)
        db.session.commit()
        return message

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Get a conversation by ID.

        Args:
            conversation_id: The conversation ID.

        Returns:
            A Conversation instance or None.
        """
        return Conversation.query.filter_by(id=conversation_id, is_active=True).first()

    def get_messages(self, conversation_id: str, limit: int = 50) -> list[Message]:
        """Get messages for a conversation.

        Args:
            conversation_id: The conversation ID.
            limit: Maximum number of messages to return.

        Returns:
            A list of Message instances.
        """
        return (
            Message.query.filter_by(conversation_id=conversation_id)
            .order_by(Message.created_at)
            .limit(limit)
            .all()
        )

    def deactivate_conversation(self, conversation_id: str) -> bool:
        """Deactivate a conversation (soft delete).

        Args:
            conversation_id: The conversation ID.

        Returns:
            True if successful, False otherwise.
        """
        conversation = self.get_conversation(conversation_id)
        if conversation:
            conversation.is_active = False
            db.session.commit()
            return True
        return False
