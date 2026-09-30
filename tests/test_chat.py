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


class TestConversationStoreOutage:
    """A database outage must not cost the caller their answer.

    The managed database can be suspended, unreachable or simply out of
    connections. Conversation storage is a convenience, not a dependency: the
    turn still works, it just cannot remember previous messages. Getting a
    usable reply matters more than remembering the last one, especially on a
    free tier that suspends idle connections.

    These previously raised out of the route, because the conversation lookup
    and history read sat outside the try block, and surfaced as an opaque 500.
    """

    @staticmethod
    def _db_down(*args, **kwargs):
        raise RuntimeError("could not connect to server: operation timed out")

    def test_chat_still_answers_when_store_is_unavailable(self, app, monkeypatch):
        monkeypatch.setattr(
            "backend.api.sauti.conversation_service.get_or_create", self._db_down
        )
        monkeypatch.setattr(
            "backend.api.sauti.conversation_service.add_message", self._db_down
        )

        client = app.test_client()
        response = client.post(
            "/api/sauti/chat", json={"message": "What is the capital of Kenya?"}
        )

        assert response.status_code == 200, (
            "a database outage must not turn a chat turn into a 500"
        )

        data = response.get_json()
        assert "error" not in data
        assert data["response"], "the caller should still receive a reply"
        assert data["engine_ok"] is False, (
            "no provider is configured in the test environment, so the turn is "
            "expected to be degraded. The point of this test is the status "
            "code, not the engine."
        )

    def test_history_failure_does_not_break_the_turn(self, app, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("connection pool exhausted")

        monkeypatch.setattr(
            "backend.api.sauti.conversation_service.get_or_create", boom
        )
        monkeypatch.setattr("backend.api.sauti._load_history", boom)

        response = app.test_client().post(
            "/api/sauti/chat", json={"message": "Name a Kenyan port city."}
        )

        assert response.status_code == 200
        assert "error" not in response.get_json()

    def test_conversation_id_is_null_when_store_is_down(self, app, monkeypatch):
        monkeypatch.setattr(
            "backend.api.sauti.conversation_service.get_or_create", self._db_down
        )
        monkeypatch.setattr(
            "backend.api.sauti.conversation_service.add_message", self._db_down
        )

        data = app.test_client().post(
            "/api/sauti/chat", json={"message": "Hello"}
        ).get_json()

        assert data["conversation_id"] is None
