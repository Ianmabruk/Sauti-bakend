"""API blueprint registration for SautiPay / SAUTI."""
from flask import Flask

from .auth import auth_bp
from .chat import chat_bp
from .health import health_bp
from .intents import intents_bp
from .languages import languages_bp
from .marketplace import marketplace_bp
from .memory import memory_bp
from .research import research_bp
from .sauti import sauti_bp
from .tools import tools_bp


def register_blueprints(app: Flask) -> None:
    """Register all API blueprints with the Flask app.

    Existing SautiPay routes are preserved. SAUTI extends /api/chat and adds
    /api/research, /api/memory and /api/tools.

    Args:
        app: The Flask application instance.
    """
    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(health_bp, url_prefix="/api")
    app.register_blueprint(chat_bp, url_prefix="/api")
    app.register_blueprint(sauti_bp, url_prefix="/api")
    app.register_blueprint(languages_bp, url_prefix="/api")
    app.register_blueprint(intents_bp, url_prefix="/api")
    app.register_blueprint(research_bp, url_prefix="/api")
    app.register_blueprint(memory_bp, url_prefix="/api")
    app.register_blueprint(marketplace_bp, url_prefix="/api")
    app.register_blueprint(tools_bp, url_prefix="/api")

    # Paystack vendor subscriptions. Additive: no existing route changes.
    from .payments import payments_bp

    app.register_blueprint(payments_bp, url_prefix="/api")

    # SautiPay frontend content system (preserved from the previous phase).
    from .admin_ops import admin_ops_bp
    from .cms import cms_bp
    from .media import media_bp

    app.register_blueprint(cms_bp, url_prefix="/api")
    app.register_blueprint(admin_ops_bp, url_prefix="/api")
    app.register_blueprint(media_bp)
