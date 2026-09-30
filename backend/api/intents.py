"""Intents endpoint for SautiPay."""
from flask import Blueprint, jsonify

from ..models import Intent
from ..db import db

intents_bp = Blueprint("intents", __name__)


@intents_bp.route("/intents", methods=["GET"])
def get_intents():
    """Get all registered intents.

    Returns:
        JSON list of registered intents.
    """
    intents = Intent.query.filter_by(is_active=True).all()
    return jsonify({
        "intents": [
            {
                "name": intent.name,
                "description": intent.description,
                "is_active": intent.is_active,
            }
            for intent in intents
        ]
    })
