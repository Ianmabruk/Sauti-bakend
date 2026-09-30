"""Security hooks for SautiPay Flask app."""
from __future__ import annotations

import hmac
import logging
from functools import wraps

from flask import Flask, request, current_app, jsonify

from .config.base import origin_is_allowed

logger = logging.getLogger(__name__)

#: Origins already reported, so a deployment-wide misconfiguration produces one
#: line per distinct origin instead of one per preflight request.
_REJECTED_ORIGINS: set[tuple[str, tuple[str, ...]]] = set()
_MAX_LOGGED_REJECTIONS = 50


def _log_rejected_origin(origin: str, allowed: tuple[str, ...]) -> bool:
    """True the first time this origin/allowlist pair is seen."""
    key = (origin, allowed)
    if key in _REJECTED_ORIGINS or len(_REJECTED_ORIGINS) >= _MAX_LOGGED_REJECTIONS:
        return False
    _REJECTED_ORIGINS.add(key)
    return True


def _resolve_admin_token() -> str:
    """Return the admin token supplied by the current request."""
    token = request.headers.get("X-Admin-Token", "").strip()
    if not token:
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
    return token


def require_admin(view):
    """Protect administrative endpoints with the configured admin token."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = current_app.config.get("ADMIN_TOKEN") or ""
        supplied = _resolve_admin_token()
        if not expected:
            return jsonify({"error": "Admin access is not configured"}), 503
        if not supplied:
            return jsonify({"error": "Administrator authentication required"}), 401
        if not hmac.compare_digest(expected, supplied):
            return jsonify({"error": "Forbidden"}), 403
        return view(*args, **kwargs)

    return wrapped


def _log_cors_configuration(app: Flask) -> None:
    """Report the effective CORS allowlist at boot.

    A browser blocked by CORS sees only an opaque console error, so an
    unnoticed misconfiguration here is expensive to diagnose: the API answers
    every health check, looks healthy, and silently refuses every real caller.
    Printing the list, and whether it still holds only local origins, makes
    that visible in the deploy log instead.
    """
    origins = app.config.get("ALLOWED_ORIGINS", [])
    if not origins:
        logger.error(
            "ALLOWED_ORIGINS is empty. Every cross-origin browser request will "
            "be refused. Set it to a comma-separated list of exact origins."
        )
        return

    logger.info("CORS allowed origins: %s", ", ".join(origins))

    if all(
        origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1")
        for origin in origins
    ):
        logger.warning(
            "ALLOWED_ORIGINS contains only local origins (%s). If this service "
            "is public, no deployed frontend will be able to call it.",
            ", ".join(origins),
        )


def register_security_hooks(app: Flask) -> None:
    """Register security-related hooks for the Flask app.

    Args:
        app: The Flask application instance.
    """

    _log_cors_configuration(app)

    @app.after_request
    def set_security_headers(response):
        """Add security headers to every response."""
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        origin = request.headers.get("Origin")
        allowed_origins = app.config.get("ALLOWED_ORIGINS", [])

        if origin and origin_is_allowed(origin, allowed_origins):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Token"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            # Without this a shared cache can serve one origin's response to
            # another, which strips the header from the wrong caller.
            response.headers["Vary"] = "Origin"
        elif origin:
            # A rejected origin is otherwise invisible: the browser reports an
            # opaque CORS failure and the server logs nothing, so the cause has
            # to be guessed at from the client side. Rate limited because a
            # misconfigured deployment would otherwise emit one line per
            # preflight from every asset request.
            if _log_rejected_origin(origin, tuple(allowed_origins)):
                logger.warning(
                    "CORS rejected origin %s. It is not in ALLOWED_ORIGINS=%s. "
                    "Add it to that variable and restart.",
                    origin,
                    ",".join(allowed_origins),
                )

        if request.method == "OPTIONS":
            response.status_code = 204
        else:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(400)
    def bad_request(error):
        """Handle 400 errors with safe messages."""
        return {"error": "Bad request"}, 400

    @app.errorhandler(401)
    def unauthorized(error):
        """Handle 401 errors with safe messages."""
        return {"error": "Unauthorized"}, 401

    @app.errorhandler(403)
    def forbidden(error):
        """Handle 403 errors with safe messages."""
        return {"error": "Forbidden"}, 403

    @app.errorhandler(404)
    def not_found(error):
        """Handle 404 errors with safe messages."""
        return {"error": "Not found"}, 404

    @app.errorhandler(429)
    def too_many_requests(error):
        """Handle 429 errors with safe messages."""
        return {"error": "Too many requests. Please try again later."}, 429

    @app.errorhandler(500)
    def internal_error(error):
        """Handle 500 errors with safe messages (no internal details)."""
        logger.error("Internal server error: %s", error, exc_info=True)
        return {"error": "Internal server error"}, 500
