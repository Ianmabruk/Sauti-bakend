"""Health check endpoint for SautiPay."""
from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health():
    """Return service health status.

    Returns:
        JSON with status and version information.
    """
    return jsonify({
        "status": "healthy",
        "service": "sautipay",
        "version": "0.1.0",
        "phase": 1,
    })
