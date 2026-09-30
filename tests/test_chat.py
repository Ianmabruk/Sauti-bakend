"""Tests for the chat endpoint."""
import pytest


class TestChatEndpoint:
    """Tests for POST /api/chat."""

    def test_chat_requires_json(self, client):
        """Test that chat endpoint requires JSON body."""
        response = client.post("/api/chat", data="not json")
        assert response.status_code == 400

    def test_chat_requires_message(self, client):
        """Test that chat endpoint requires a message."""
        response = client.post("/api/chat", json={})
        assert response.status_code == 400

    def test_chat_accepts_empty_message_after_strip(self, client):
        """Test that chat endpoint rejects empty messages."""
        response = client.post("/api/chat", json={"message": "   "})
        assert response.status_code == 400

    def test_chat_rejects_long_message(self, client):
        """Test that chat endpoint rejects messages over 5000 chars."""
        long_message = "a" * 5001
        response = client.post("/api/chat", json={"message": long_message})
        assert response.status_code == 400

    def test_chat_returns_response(self, client):
        """Test that chat endpoint returns a response."""
        response = client.post("/api/chat", json={"message": "Hello"})
        assert response.status_code == 200
        data = response.get_json()
        assert "response" in data
        assert "language" in data
        assert "intent" in data
        assert "confidence" in data
        assert "sources" in data

    def test_chat_with_auto_language(self, client):
        """Test that chat endpoint detects language automatically."""
        response = client.post("/api/chat", json={"message": "Hujumba"})
        assert response.status_code == 200
        data = response.get_json()
        assert data["language"] in ["en", "sw"]

    def test_chat_with_explicit_language(self, client):
        """Test that chat endpoint respects explicit language."""
        response = client.post("/api/chat", json={
            "message": "Hello",
            "language": "en"
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data["language"] == "en"

    def test_chat_with_conversation_id(self, client):
        """Test that chat endpoint accepts conversation_id."""
        response = client.post("/api/chat", json={
            "message": "Hello",
            "conversation_id": "test-conv-123"
        })
        assert response.status_code == 200
        data = response.get_json()
        assert "conversation_id" in data
