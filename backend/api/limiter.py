"""The process-wide rate limiter.

Flask-Limiter's ``@limiter.limit`` decorator is applied at import time, so
every route that wants a limit needs a reference to the *same* limiter
instance. Constructing one per module would create several independent
buckets, and the first one to run would exhaust storage or silently miss.

This module owns the single instance. :mod:`backend.app` initialises it against
the Flask app; route modules import it and decorate.

The limiter is deliberately created with no default limits. Adding a global
default would start rejecting ordinary page and asset requests, so limits are
opt-in per route.
"""
from __future__ import annotations

import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

#: Storage backend for counters. Memory is fine for a single-process dev
#: server; a multi-process deployment must point this at Redis or similar,
#: otherwise each worker enforces its own independent budget.
STORAGE_URI = os.environ.get("RATE_LIMIT_STORAGE", "memory://")


def warn_if_in_memory_in_production(logger=None) -> bool:
    """Warn when an in-process counter store is used in production.

    Returns True when it warned.

    ``memory://`` is not a shared store, so every gunicorn worker keeps a
    private tally. Two consequences, both of which are invisible until someone
    is attacked:

    * The configured limit is per worker, so ``5 per minute`` permits roughly
      ``5 x workers`` — the limit is not the number anyone reading the config
      would assume it is.
    * Counters do not survive a restart, so a deploy resets every budget and
      removes any limit on how often one address can retry a request over time.

    A warning rather than a hard failure, because refusing to boot would take
    down a working deployment over a hardening gap. Nothing here is left
    unwarned: if this returns True in production, the limiters are not doing what
    the configuration says they are doing.
    """
    import logging
    import sys

    log = logger or logging.getLogger(__name__)

    if not STORAGE_URI.startswith("memory://"):
        return False

    if os.environ.get("FLASK_ENV") != "production":
        return False

    workers = os.environ.get("WEB_CONCURRENCY")
    if not workers:
        # render.backend.yaml pins --workers 2 in the start command.
        workers = "2 (gunicorn start command)"

    log.warning(
        "=" * 72,
        extra={"stream": sys.stderr},
    )
    log.warning(
        "RATE_LIMIT_STORAGE is %r and FLASK_ENV=production. Counters are "
        "per-process, not shared.",
        STORAGE_URI,
        extra={"stream": sys.stderr},
    )
    log.warning(
        "Each worker (%s) enforces its own budget, so the effective limit is "
        "roughly that many times the configured value, and every restart "
        "resets all counters.",
        workers,
        extra={"stream": sys.stderr},
    )
    log.warning(
        "Set RATE_LIMIT_STORAGE to a shared backend, e.g. "
        "redis://:<password>@<host>:6379/0, before this faces real traffic.",
        extra={"stream": sys.stderr},
    )
    log.warning("=" * 72, extra={"stream": sys.stderr})
    return True


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=STORAGE_URI,
)

__all__ = ["limiter", "STORAGE_URI", "get_remote_address", "warn_if_in_memory_in_production"]
