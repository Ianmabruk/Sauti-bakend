"""Security hooks for SautiPay Flask app."""
from __future__ import annotations

import hmac
import logging
from functools import wraps

from flask import Flask, request, current_app, jsonify

logger = logging.getLogger(__name__)


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


def register_security_hooks(app: Flask) -> None:
    """Register security-related hooks with the Flask app.

    Args:
        app: The Flask application instance.
    """
    @app.after_request
    def set_security_headers(response):
        """Add security headers to every response."""
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        origin = request.headers.get("Origin")
        allowed_origins = app.config.get("ALLOWED_ORIGINS", [])
        if origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Token"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Vary"] = "Origin"

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
