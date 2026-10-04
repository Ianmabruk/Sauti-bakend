"""WSGI entry point for SautiPay Flask app."""
import logging
import os

from backend.app import create_app
from backend.db import db
from backend.config import get_config

app = create_app(get_config())

logger = logging.getLogger(__name__)

# Schema creation is a development convenience and nothing more. It is skipped
# when TESTING is set, because the test suite owns its own schema and an
# import-time create_all() would collide with it.
#
# It is also skipped in production, where migrations own the schema and
# `alembic upgrade head` runs before gunicorn starts (see render.backend.yaml).
#
# create_all() cannot express an upgrade. It creates whatever tables the models
# happen to declare and nothing else, so it cannot add a column to an existing
# table, and it cannot tell an intended empty table from a table that should have
# been altered. A deploy that relies on it therefore leaves every existing table
# exactly as it was, and the new code starts up against a schema it does not
# match. With the subscription tables that failure is not a startup error: the
# first webhook arrives, the INSERT names a column the database does not have, and
# the one event that would have activated a paid subscription is the one that
# fails.
#
# Skipping it in production also removes a startup race. Under gunicorn every
# worker imports this module, so N workers race to run it; SQLAlchemy checks for
# existence first, but two workers can both observe a missing table and both issue
# CREATE, and the loser raises, taking the worker down. Tolerating that race was
# the previous behaviour, which is an admission that the operation does not
# belong at import time.
_is_production = os.environ.get("FLASK_ENV") == "production"

if not app.config.get("TESTING") and not _is_production:
    with app.app_context():
        try:
            db.create_all()
        except Exception:  # noqa: BLE001 - a lost race must not stop boot
            logger.warning(
                "Schema creation did not complete; assuming another worker "
                "or an earlier run created it.",
                exc_info=True,
            )
elif _is_production:
    logger.info(
        "FLASK_ENV=production: skipping create_all(). The schema is owned by "
        "Alembic; run `alembic upgrade head` as part of the deploy."
    )

# The rate limiter is created at import time with whatever RATE_LIMIT_STORAGE
# says. If that is an in-process store in production, the configured limits are
# not the limits actually being enforced, and nothing at runtime would say so.
if not app.config.get("TESTING"):
    from backend.api.limiter import warn_if_in_memory_in_production

    warn_if_in_memory_in_production(logger)

# Fail loudly rather than silently signing sessions with a public constant.
if (
    not app.config.get("TESTING")
    and os.environ.get("SECRET_KEY", "dev-secret-change-me") == "dev-secret-change-me"
):
    logger.warning(
        "SECRET_KEY is unset or still the development default. Set a random "
        "value in production or anything Flask signs is forgeable."
    )

for key in ["DATABASE_URL", "GROQ_API_KEY", "SECRET_KEY", "ADMIN_TOKEN"]:
    print(f"{key}: {'SET' if os.environ.get(key) else 'MISSING'}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)