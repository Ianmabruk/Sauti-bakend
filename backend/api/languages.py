"""Languages endpoint for SautiPay."""
from flask import Blueprint, jsonify

from ..services.language import LanguageService

languages_bp = Blueprint("languages", __name__)
language_service = LanguageService()


@languages_bp.route("/languages", methods=["GET"])
def get_languages():
    """Get all supported languages.

    Returns:
        JSON list of supported languages.
    """
    languages = language_service.get_supported_languages()
    return jsonify({
        "languages": [
            {
                "code": lang.code,
                "name": lang.name,
                "native_name": lang.native_name,
                "is_active": lang.is_active,
                "has_verified_data": lang.has_verified_data,
            }
            for lang in languages
        ]
    })
