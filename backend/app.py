"""Flask application factory for SautiPay."""
import logging
import sys

from flask import Flask
from flask_talisman import Talisman
from werkzeug.middleware.proxy_fix import ProxyFix

from .api import register_blueprints
from .api.limiter import limiter
from .config import Config
from .db import db, migrate
from .security import register_security_hooks


def configure_logging(level_name: str = "INFO") -> None:
    """Apply ``LOG_LEVEL`` to the root logger.

    Without this, Python leaves the root logger at WARNING and every INFO line
    is silently discarded. That matters here because the per-request
    ``rid=`` trace through the AI pipeline is logged at INFO, so an
    unconfigured app looks like it is doing nothing.

    The format keeps ``rid=`` at the front of each line so a whole request can
    be reconstructed by grepping for one id.

    Args:
        level_name: A logging level name such as ``INFO`` or ``DEBUG``.
    """
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    root = logging.getLogger()
    # Replace rather than append, so a reload never doubles every line.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)

    # These libraries are chatty and say nothing useful about this app.
    for noisy in ("httpx", "httpcore", "urllib3", "apscheduler", "werkzeug"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


def create_app(config_object: type = Config) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.from_object(config_object)

    @app.get("/")
    def index():
        """Serve a basic root response for smoke checks and Render health probes."""
        return {"status": "ok", "service": "sautipay", "message": "API is running"}

    configure_logging(app.config.get("LOG_LEVEL", "INFO"))

    # Database
    db.init_app(app)
    migrate.init_app(app, db)

    # Behind a TLS-terminating proxy (Render, Heroku, nginx, a load balancer)
    # the connection to this process is plain HTTP, so Flask would report
    # every request as insecure and build http:// absolute URLs. Trusting one
    # proxy hop restores the original scheme and client address. The header is
    # only meaningful because the proxy overwrites it; on a direct connection
    # there is nothing to misread.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Security headers (skip in testing to avoid HTTPS redirects)
    if not app.config.get("TESTING"):
        Talisman(
            app,
            content_security_policy=None,
            frame_options="DENY",
            # Render terminates TLS at its proxy and speaks HTTP to the
            # container. Forcing HTTPS here would redirect Render's own plain
            # HTTP health check and fail the deploy.
            force_https=False,
        )

    # Rate limiting. The limiter instance lives in backend/api/limiter.py so
    # that route modules can decorate themselves at import time.
    limiter.init_app(app)
    app.extensions["limiter"] = limiter

    # Blueprints
    register_blueprints(app)

    # Security hooks (error handlers, logging)
    register_security_hooks(app)

    return app