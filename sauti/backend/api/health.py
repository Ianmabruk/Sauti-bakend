"""Health check endpoint for SautiPay."""
from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health():
    """Return a smoke-test-friendly health payload for Render and browsers."""
    return jsonify({
        "status": "ok",
        "service": "sautipay",
        "version": "0.1.0",
        "phase": 1,
    })


@health_bp.route("/", methods=["GET"])
def api_root():
    """Provide a root smoke endpoint for the backend and deployment checks."""
    return jsonify({
        "status": "ok",
        "service": "sautipay",
        "message": "API is running",
        "version": "0.1.0",
    })
