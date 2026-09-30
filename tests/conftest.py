"""Pytest configuration and fixtures for SautiPay tests."""
import os
import sys
import pytest
from flask import Flask

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app
from backend.db import db
from backend.config import TestingConfig

#: Provider credentials that must never be present while the suite runs.
#:
#: backend/config/base.py loads .env at import time, so a developer's real
#: GROQ_API_KEY / GEMINI_API_KEY would otherwise leak into tests. That made the
#: suite (a) assert against the developer's real provider and (b) issue live
#: network calls. Tests that need a provider set it explicitly.
_PROVIDER_KEYS = (
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "LLM_API_KEY",
    "SEARCH_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_provider_keys(monkeypatch):
    """Strip real provider credentials from the environment for every test.

    Keeps the suite hermetic and deterministic: no network calls, no
    dependence on whichever keys happen to exist in a local .env.
    """
    for key in _PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)

    # Settings are constructed per-instance from os.environ, but the
    # process-wide cache would otherwise leak between tests.
    from backend.config.settings import get_settings

    get_settings(refresh=True)
    yield
    get_settings(refresh=True)


@pytest.fixture
def app():
    """Create a Flask application for testing."""
    app = create_app(TestingConfig)
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


@pytest.fixture
def db_session(app):
    """Create a database session for testing."""
    with app.app_context():
        yield db.session
