"""Tests for the health endpoint."""
import pytest


class TestHealthEndpoint:
    """Tests for GET /api/health and the root smoke endpoint."""

    def test_root_returns_200(self, client):
        """The API root should respond to simple smoke checks."""
        response = client.get("/")
        assert response.status_code == 200

    def test_health_returns_200(self, client):
        """Test that health endpoint returns 200."""
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_health_returns_ok_status(self, client):
        """Test that health endpoint returns a smoke-test-friendly status."""
        response = client.get("/api/health")
        data = response.get_json()
        assert data["status"] == "ok"

    def test_health_returns_service_name(self, client):
        """Test that health endpoint returns service name."""
        response = client.get("/api/health")
        data = response.get_json()
        assert data["service"] == "sautipay"

    def test_health_returns_version(self, client):
        """Test that health endpoint returns version."""
        response = client.get("/api/health")
        data = response.get_json()
        assert "version" in data
