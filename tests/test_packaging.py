"""Tests for the packaging metadata.

Two failure modes this guards against:

A dependency added to pyproject.toml but not mirrored into requirements.txt,
which breaks the Render build that installs from requirements.txt. And a
dependency present in one file but not the other, which produces two different
environments depending on which build path ran.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
REQUIREMENTS = REPO_ROOT / "requirements.txt"


def _normalise(requirement: str) -> str:
    """Reduce a requirement to its bare distribution name.

    Strips extras, version specifiers, markers and environment markers so that
    `Flask[async]>=3.0, <4` and `flask` compare equal.
    """
    name = requirement.split(";", 1)[0].strip()
    name = re.split(r"[<>=!~\[\s]", name, maxsplit=1)[0]
    return name.strip().lower().replace("_", "-")


def _pyproject_runtime_deps() -> set[str]:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    return {_normalise(d) for d in data["project"]["dependencies"]}


def _requirements_file_deps() -> set[str]:
    names: set[str] = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        names.add(_normalise(stripped))
    return names


class TestRequirementsStayInSync:
    def test_pyproject_exists_and_parses(self):
        assert PYPROJECT.is_file(), "pyproject.toml is the source of truth"
        assert _pyproject_runtime_deps(), "expected runtime dependencies"

    def test_requirements_file_exists(self):
        assert REQUIREMENTS.is_file(), (
            "Render's Web Service build command installs from requirements.txt"
        )

    def test_no_dependency_is_missing_from_requirements(self):
        missing = _pyproject_runtime_deps() - _requirements_file_deps()
        assert not missing, (
            f"in pyproject.toml but not requirements.txt: {sorted(missing)}"
        )

    def test_no_extra_dependency_in_requirements(self):
        extra = _requirements_file_deps() - _pyproject_runtime_deps()
        assert not extra, (
            f"in requirements.txt but not pyproject.toml: {sorted(extra)}"
        )

    def test_gunicorn_is_present(self):
        """The Render start command is gunicorn; it must be installed."""
        assert "gunicorn" in _requirements_file_deps()
        assert "gunicorn" in _pyproject_runtime_deps()

    def test_optional_extras_are_not_included(self):
        """The JARVIS audio stack must not land on the API server."""
        forbidden = {"edge-tts", "sounddevice", "pyaudio", "soundfile"}
        assert not (forbidden & _requirements_file_deps())

class TestConnectionPool:
    """Pool settings must survive a connection dropped by an intermediary.

    Render, Heroku, nginx and Neon's own pooler all close idle connections on
    their own schedule. SQLAlchemy's default leaves pool_recycle disabled, so a
    dead socket stays in the pool until a request borrows it, and that request
    then fails deep in the driver with an OperationalError that reads like
    corruption rather than staleness.
    """

    def _postgres_options(self):
        """Options as they apply to a network database, regardless of this
        machine's DATABASE_URL."""
        from backend.config.base import parse_pool_options

        return parse_pool_options("postgresql://user:pw@host/db")

    def test_pre_ping_is_enabled(self):
        assert self._postgres_options()["pool_pre_ping"] is True

    def test_pool_recycle_is_finite(self):
        recycle = self._postgres_options()["pool_recycle"]
        assert 0 < recycle <= 900, "recycle must be on, and short enough to beat the far end"

    def test_sqlite_gets_no_pool_sizing(self):
        """SQLite pools are single connections and reject these kwargs."""
        from backend.config.base import parse_pool_options

        assert parse_pool_options("sqlite:///sautipay.db") == {}
        assert parse_pool_options("sqlite:///:memory:") == {}

    def test_engine_options_match_the_database_url(self):
        from backend.config.base import Config

        url = Config.SQLALCHEMY_DATABASE_URI
        expected = {} if url.startswith("sqlite") else self._postgres_options()
        assert Config.SQLALCHEMY_ENGINE_OPTIONS == expected

    @staticmethod
    def _app():
        from backend.app import create_app
        from backend.config.base import Config

        return create_app(Config)

    def test_engine_actually_receives_the_options(self):
        with self._app().app_context():
            from backend.db import db

            assert db.engine.pool._pre_ping is True
            assert db.engine.pool._recycle

    def test_query_survives_a_returned_connection(self):
        """A connection handed back and borrowed again must still work."""
        from sqlalchemy import text

        with self._app().app_context():
            from backend.db import db

            assert db.session.execute(text("select 1")).scalar() == 1
            raw = db.engine.raw_connection()
            raw.close()
            assert db.session.execute(text("select 1")).scalar() == 1


class TestEnvPresenceReporting:
    """/api/sauti/health must explain itself without leaking a credential.

    "The engine says offline" is otherwise ambiguous between the variable
    being absent, misspelled, saved but not yet deployed, or set but rejected
    by the provider. Naming the variables that are present separates those
    immediately.
    """

    def test_reports_a_boolean_per_expected_variable(self):
        from backend.config.settings import get_settings

        present = get_settings().env_presence()
        assert "GROQ_API_KEY" in present
        assert all(isinstance(v, bool) for v in present.values())

    def test_never_returns_a_credential_value(self):
        from backend.config.settings import get_settings

        present = get_settings().env_presence()
        # A value would make the repr longer than a boolean and could carry a
        # secret prefix. Nothing here is ever longer than 5 characters.
        assert all(len(str(v)) <= 5 for v in present.values())

    def test_appears_in_the_public_summary(self):
        from backend.config.settings import get_settings

        assert "env_present" in get_settings().public_summary()

    def test_absent_variable_is_reported_absent(self, monkeypatch):
        from backend.config.settings import get_settings

        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        get_settings(refresh=True)
        try:
            assert get_settings().env_presence()["GROQ_API_KEY"] is False
        finally:
            get_settings(refresh=True)
