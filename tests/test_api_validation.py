"""Tests for API input validation and error handling."""
import pytest


class TestAPIValidation:
    """Tests for API input validation."""

    def test_chat_rejects_non_json(self, client):
        """Test that chat endpoint rejects non-JSON requests."""
        response = client.post("/api/chat", data="plain text", content_type="text/plain")
        assert response.status_code == 400

    def test_chat_rejects_empty_body(self, client):
        """Test that chat endpoint rejects empty body."""
        response = client.post("/api/chat", data="")
        assert response.status_code == 400

    def test_health_endpoint_method(self, client):
        """Test that health endpoint only accepts GET."""
        response = client.post("/api/health")
        assert response.status_code == 405

    def test_languages_endpoint_returns_list(self, client):
        """Test that languages endpoint returns a list."""
        response = client.get("/api/languages")
        assert response.status_code == 200
        data = response.get_json()
        assert "languages" in data
        assert isinstance(data["languages"], list)

    def test_intents_endpoint_returns_list(self, client):
        """Test that intents endpoint returns a list."""
        response = client.get("/api/intents")
        assert response.status_code == 200
        data = response.get_json()
        assert "intents" in data
        assert isinstance(data["intents"], list)

    def test_404_for_unknown_route(self, client):
        """Test that unknown routes return 404."""
        response = client.get("/api/unknown")
        assert response.status_code == 404
