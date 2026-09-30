"""Tests for the health endpoint."""
import pytest


class TestHealthEndpoint:
    """Tests for GET /api/health."""

    def test_health_returns_200(self, client):
        """Test that health endpoint returns 200."""
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_health_returns_healthy_status(self, client):
        """Test that health endpoint returns healthy status."""
        response = client.get("/api/health")
        data = response.get_json()
        assert data["status"] == "healthy"

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
