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

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=STORAGE_URI,
)

__all__ = ["limiter", "STORAGE_URI", "get_remote_address"]
