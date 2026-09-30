"""Tests for CORS configuration and enforcement.

The behaviours here were all found the hard way, so each one is pinned:

- A trailing slash in ALLOWED_ORIGINS silently dropped the origin from the
  allowlist. Browsers never send one, so a correctly written config was
  rejected and the only symptom was an opaque console error.
- Production accepted the localhost fallback, so the service booted, passed its
  health check and refused every real caller.
- ``*`` would hand a provider-keyed API to every site on the internet.
"""
from __future__ import annotations

import os

import pytest

from backend.config.base import parse_allowed_origins


class TestParseAllowedOrigins:
    def test_plain_list(self):
        assert parse_allowed_origins("https://a.example,https://b.example") == [
            "https://a.example",
            "https://b.example",
        ]

    def test_strips_surrounding_whitespace(self):
        assert parse_allowed_origins("  https://a.example ,  https://b.example ") == [
            "https://a.example",
            "https://b.example",
        ]

    def test_strips_trailing_slash(self):
        """The bug. Browsers send no trailing slash, so one here never matches."""
        assert parse_allowed_origins("https://a.example/") == ["https://a.example"]

    def test_strips_repeated_trailing_slashes(self):
        assert parse_allowed_origins("https://a.example///") == ["https://a.example"]

    def test_slash_and_whitespace_together(self):
        assert parse_allowed_origins(" https://a.example/ , https://b.example// ") == [
            "https://a.example",
            "https://b.example",
        ]

    def test_drops_empty_entries(self):
        assert parse_allowed_origins("https://a.example,,  ,https://b.example") == [
            "https://a.example",
            "https://b.example",
        ]

    def test_preserves_port(self):
        assert parse_allowed_origins("http://localhost:5173") == ["http://localhost:5173"]

    def test_rejects_wildcard(self):
        with pytest.raises(ValueError, match="must not contain"):
            parse_allowed_origins("https://a.example,*")

    def test_empty_string_yields_empty_list(self):
        assert parse_allowed_origins("") == []


def _app_with_origins(monkeypatch, raw: str):
    """Build a test client whose allowlist comes from `raw`.

    Subclasses the real Config so the database and every other setting are
    inherited; a bare class would fail on the missing SQLAlchemy URI.
    """
    import backend.config.base as base

    monkeypatch.setenv("ALLOWED_ORIGINS", raw)
    cfg = type("_CorsCfg", (base.Config,), {"ALLOWED_ORIGINS": parse_allowed_origins(raw)})

    from backend.app import create_app

    return create_app(cfg).test_client()


def _preflight(client, origin: str):
    return client.options(
        "/api/marketplace/popular",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


class TestCorsPreflight:
    def test_allowed_origin_is_echoed(self, monkeypatch):
        client = _app_with_origins(monkeypatch, "https://app.example")
        r = _preflight(client, "https://app.example")
        assert r.headers["Access-Control-Allow-Origin"] == "https://app.example"

    def test_configured_with_trailing_slash_still_matches(self, monkeypatch):
        """The regression: this used to return no header at all."""
        client = _app_with_origins(monkeypatch, "https://app.example/")
        r = _preflight(client, "https://app.example")
        assert r.headers.get("Access-Control-Allow-Origin") == "https://app.example"

    def test_disallowed_origin_gets_no_header(self, monkeypatch):
        client = _app_with_origins(monkeypatch, "https://app.example")
        r = _preflight(client, "https://evil.example")
        assert "Access-Control-Allow-Origin" not in r.headers

    def test_prefix_is_not_a_match(self, monkeypatch):
        """An attacker origin must not match by prefix."""
        client = _app_with_origins(monkeypatch, "https://app.example")
        r = _preflight(client, "https://app.example.evil.com")
        assert "Access-Control-Allow-Origin" not in r.headers

    def test_options_returns_204(self, monkeypatch):
        client = _app_with_origins(monkeypatch, "https://app.example")
        assert _preflight(client, "https://app.example").status_code == 204

    def test_methods_and_headers_advertised(self, monkeypatch):
        client = _app_with_origins(monkeypatch, "https://app.example")
        r = _preflight(client, "https://app.example")
        assert "POST" in r.headers["Access-Control-Allow-Methods"]
        assert "X-Admin-Token" in r.headers["Access-Control-Allow-Headers"]

    def test_vary_origin_set(self, monkeypatch):
        """Without this a shared cache can serve one origin's response to another."""
        client = _app_with_origins(monkeypatch, "https://app.example")
        r = _preflight(client, "https://app.example")
        assert "Origin" in r.headers.get("Vary", "")

    def test_real_get_request_is_allowed(self, monkeypatch, app):
        client = app.test_client()
        r = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
        assert r.status_code == 200


class TestProductionValidation:
    """get_config() validates on call, so these set the environment directly.

    The module is deliberately not reloaded here: reloading re-runs
    load_dotenv, which would restore any deleted variable from the repository's
    .env file and mask exactly what these tests are checking.
    """

    @pytest.fixture
    def base(self, monkeypatch):
        import backend.config.base as base

        monkeypatch.setenv("FLASK_ENV", "production")
        return base

    def test_production_refuses_without_allowed_origins(self, base, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "s" * 40)
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS is required"):
            base.get_config()

    def test_production_refuses_without_secret_key(self, base, monkeypatch):
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example")
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SECRET_KEY is required"):
            base.get_config()

    def test_production_refuses_default_secret_key(self, base, monkeypatch):
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example")
        monkeypatch.setenv("SECRET_KEY", "dev-secret-change-me")
        with pytest.raises(RuntimeError, match="SECRET_KEY is required"):
            base.get_config()

    def test_production_accepts_a_fully_specified_config(self, base, monkeypatch):
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example")
        monkeypatch.setenv("SECRET_KEY", "s" * 40)
        assert base.get_config() is base.ProductionConfig

    def test_development_never_raises(self, base, monkeypatch):
        """Validation must not run at import, or local dev breaks."""
        monkeypatch.setenv("FLASK_ENV", "development")
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        monkeypatch.setenv("SECRET_KEY", "dev-secret-change-me")
        assert base.get_config() is base.DevelopmentConfig

class TestObservationCap:
    """Research observations must be bounded.

    Each observation carries ~180 characters of context into the tool payload,
    and that payload is replayed into the model on every later turn of the tool
    loop. Unbounded, a figure-dense page produced enough context to trip the
    provider's input-tokens-per-minute limit, which discarded the research and
    the answer it was gathered for.
    """

    def _dense_source(self):
        from backend.services.citations import Source

        body = "KSh 129.60 per USD and EUR 135.20 and GBP 160.10 and JPY 0.90 and ZAR 22.30 " * 40
        return Source(
            title="Rates",
            url="https://example.com/rates",
            domain="example.com",
            snippet="",
            content=body,
        )

    def test_observations_are_capped(self):
        from backend.services.research.pipeline import _extract_observations

        observations, _ = _extract_observations([self._dense_source()], "usd to kes", 12)
        assert len(observations) == 12

    def test_cap_of_one_keeps_only_the_headline_figure(self):
        from backend.services.research.pipeline import _extract_observations

        observations, _ = _extract_observations([self._dense_source()], "usd to kes", 1)
        assert len(observations) == 1
        assert "129.60" in observations[0]["raw"]

    def test_cap_is_configurable(self):
        from backend.services.research.pipeline import _extract_observations

        source = self._dense_source()
        assert len(_extract_observations([source], "x", 5)[0]) == 5
        assert len(_extract_observations([source], "x", 40)[0]) == 40

    def test_setting_has_a_sane_default(self):
        from backend.config.settings import Settings

        assert 0 < Settings().research_max_observations <= 50


class TestOriginMatching:
    """Matching rules for the allowlist, including the wildcard form."""

    ALLOWED = [
        "https://sautiai1.netlify.app",
        "https://*.netlify.app",
        "http://localhost:3000",
    ]

    @pytest.mark.parametrize(
        "origin",
        [
            "https://sautiai1.netlify.app",
            "https://sautiai1.netlify.app/",
            "HTTPS://SAUTIAI1.NETLIFY.APP",
            "http://localhost:3000",
            # A Netlify deploy preview gets a fresh hostname on every branch.
            "https://deploy-preview-7a3.netlify.app",
        ],
    )
    def test_allowed(self, origin):
        from backend.config.base import origin_is_allowed

        assert origin_is_allowed(origin, self.ALLOWED) is True

    @pytest.mark.parametrize(
        "origin",
        [
            "https://evil.example.com",
            # Bare registrable domain: the wildcard must require a subdomain.
            "https://netlify.app",
            # Suffix confusion, the classic way a naive endswith() goes wrong.
            "https://evil.netlify.app.evil.com",
            # Deep subdomains are not covered by a one-label wildcard.
            "https://a.b.netlify.app",
            # Scheme is part of an origin and is not wildcarded.
            "http://sautiai1.netlify.app",
            # Port is part of an origin too.
            "https://sautiai1.netlify.app:8443",
            "*",
            "",
        ],
    )
    def test_rejected(self, origin):
        from backend.config.base import origin_is_allowed

        assert origin_is_allowed(origin, self.ALLOWED) is False

    def test_exact_match_still_works_without_any_wildcard(self):
        from backend.config.base import origin_is_allowed

        allowed = ["https://app.example.com"]
        assert origin_is_allowed("https://app.example.com", allowed) is True
        assert origin_is_allowed("https://other.example.com", allowed) is False

    def test_empty_allowlist_rejects_everything(self):
        from backend.config.base import origin_is_allowed

        assert origin_is_allowed("https://app.example.com", []) is False


class TestRejectedOriginIsLogged:
    """A rejected origin must be visible in the server log.

    The browser reports an opaque CORS failure and the client cannot know why.
    Without a server-side log the only clue is the allowed list, which the
    operator has to guess at. These tests patch the logger rather than using
    caplog, because create_app reconfigures logging and detaches pytest's
    handler from the backend.security logger.
    """

    def test_dedupe_helper_reports_each_origin_once(self):
        from backend.security import _log_rejected_origin

        allowed = ("https://ok.example",)
        assert _log_rejected_origin("https://a.example", allowed) is True
        assert _log_rejected_origin("https://a.example", allowed) is False
        assert _log_rejected_origin("https://b.example", allowed) is True

    def test_rejection_emits_a_warning_naming_the_variable(self, monkeypatch):
        from unittest import mock

        from backend.app import create_app
        from backend.config.base import Config

        cfg = type("_Cfg", (Config,), {"ALLOWED_ORIGINS": ["https://ok.example"]})
        client = create_app(cfg).test_client()

        with mock.patch("backend.security.logger") as logger:
            client.options(
                "/api/health",
                headers={
                    "Origin": "https://nope.example",
                    "Access-Control-Request-Method": "GET",
                },
            )

        warnings = [
            call.args[0] % call.args[1:] if len(call.args) > 1 else call.args[0]
            for call in logger.warning.call_args_list
        ]
        joined = " ".join(str(w) for w in warnings)
        assert "CORS rejected origin" in joined
        assert "nope.example" in joined
        assert "ALLOWED_ORIGINS" in joined

    def test_allowed_origin_does_not_warn(self, monkeypatch):
        from unittest import mock

        from backend.app import create_app
        from backend.config.base import Config

        cfg = type("_Cfg", (Config,), {"ALLOWED_ORIGINS": ["https://ok.example"]})
        client = create_app(cfg).test_client()

        with mock.patch("backend.security.logger") as logger:
            client.options(
                "/api/health",
                headers={
                    "Origin": "https://ok.example",
                    "Access-Control-Request-Method": "GET",
                },
            )

        joined = " ".join(str(c) for c in logger.warning.call_args_list)
        assert "CORS rejected" not in joined
