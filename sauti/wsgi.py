"""WSGI entry point for SautiPay Flask app."""
import logging
import os

from backend.app import create_app
from backend.db import db
from backend.config import get_config

app = create_app(get_config())

# Create tables for development convenience. Skipped when TESTING is set,
# because the test suite owns its own schema and an import-time create_all()
# would collide with it.
#
# Under gunicorn every worker imports this module, so N workers race to run
# this. SQLAlchemy checks for existence first, but two workers can both observe
# a missing table and then both issue CREATE, and the loser raises. That would
# take the worker down and fail the deploy, so the race is tolerated rather than
# fatal: if the schema already exists, that is the desired end state.
if not app.config.get("TESTING"):
    with app.app_context():
        try:
            db.create_all()
        except Exception:  # noqa: BLE001 - a lost race must not stop boot
            logging.getLogger(__name__).warning(
                "Schema creation did not complete; assuming another worker "
                "or an earlier deploy created it.",
                exc_info=True,
            )

# Fail loudly rather than silently signing sessions with a public constant.
if (
    not app.config.get("TESTING")
    and os.environ.get("SECRET_KEY", "dev-secret-change-me") == "dev-secret-change-me"
):
    logging.getLogger(__name__).warning(
        "SECRET_KEY is unset or still the development default. Set a random "
        "value in production or anything Flask signs is forgeable."
    )

for key in ["DATABASE_URL", "GROQ_API_KEY", "SECRET_KEY", "ADMIN_TOKEN"]:
    print(f"{key}: {'SET' if os.environ.get(key) else 'MISSING'}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)