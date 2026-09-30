"""Configuration management for SautiPay."""
import os
from pathlib import Path

# Load .env before any environment variable is read. Without this a .env file
# is silently ignored, which is the single most confusing failure mode when
# setting up an API key. Real environment variables still win over .env.
try:
    from dotenv import load_dotenv

    _REPO_ROOT = Path(__file__).resolve().parents[2]
    load_dotenv(_REPO_ROOT / ".env")
    load_dotenv(_REPO_ROOT / ".env.local")
except ImportError:  # pragma: no cover - python-dotenv is a declared dependency
    pass


#: Used when ALLOWED_ORIGINS is unset. Local development only.
_DEV_ORIGINS = "http://localhost:5173,http://localhost:3000"


def parse_allowed_origins(raw: str) -> list[str]:
    """Turn a comma-separated ALLOWED_ORIGINS value into a clean origin list.

    Normalisation matters more than it looks. A browser sends the ``Origin``
    header as a scheme, host and port and nothing else: no trailing slash, ever.
    An entry written as ``https://app.example.com/`` therefore never matches and
    silently removes that origin from the allowlist, which surfaces to the user
    only as an opaque CORS error indistinguishable from a mistyped hostname.

    Trailing slashes are stripped, surrounding whitespace is removed, empty
    entries are dropped, and any wildcard entry is rejected rather than being
    quietly honoured.
    """
    origins: list[str] = []
    for candidate in raw.split(","):
        origin = candidate.strip().rstrip("/")
        if not origin:
            continue
        if origin == "*":
            # A wildcard would hand every site on the internet access to a
            # provider-keyed API. Refuse it here where the operator sees why.
            raise ValueError(
                "ALLOWED_ORIGINS must not contain '*'. List each origin "
                "explicitly, e.g. https://app.example.com"
            )
        origins.append(origin)
    return origins


def parse_pool_options(database_url: str) -> dict:
    """Build the SQLAlchemy engine options for a database URL.

    Connection pooling exists to reuse sockets across requests, which is what
    makes talking to a network database viable. SQLAlchemy's defaults leave
    pool_recycle disabled, so a connection is held open indefinitely. Any proxy,
    load balancer or serverless platform in front of the database closes idle
    connections on its own schedule, and those dead sockets stay in the pool
    until something borrows one. The next request then fails deep in the
    driver, with "SSL error: decryption failed or bad record mac" or "server
    closed the connection unexpectedly", which reads like corruption rather
    than the stale connection it actually is.

    pool_pre_ping validates a connection before handing it out and discards it
    if the server is gone, so the request transparently opens a fresh one.
    pool_recycle caps connection age so it never reaches the age at which the
    far end drops it.

    These are sizing options for a network client. A SQLite pool is a single
    connection by design and rejects pool_size, max_overflow and pool_timeout
    outright, so the whole block is suppressed for a SQLite URL. That keeps
    local development and the test suite, which use SQLite or an in-memory
    database, on the same code path as production.

    Args:
        database_url: The configured database URL, inspected only to decide
            whether pool sizing is meaningful.

    Returns:
        A dict of engine options, empty for SQLite.
    """
    if database_url.startswith("sqlite"):
        return {}

    return {
        "pool_pre_ping": True,
        "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", "300")),
        "pool_size": int(os.environ.get("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "10")),
        # Bounds how long a request waits for a connection instead of hanging
        # until the far end gives up.
        "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "10")),
    }


def origin_is_allowed(origin: str, allowed: list[str]) -> bool:
    """True when ``origin`` matches an entry in the allowlist.

    Matching is exact, case-insensitive on scheme and host, and supports one
    wildcard form: ``https://*.netlify.app``. Static hosts issue a different
    hostname for every branch and deploy preview, so an exact-match-only list
    has to be edited every time one is opened; the wildcard keeps those
    working while still pinning the scheme and the registrable domain.

    A bare ``*`` is deliberately not supported. This API spends a provider key
    on every call, so the allowlist stays explicit.
    """
    if not origin:
        return False

    candidate = origin.strip().rstrip("/").lower()

    for entry in allowed:
        pattern = entry.strip().rstrip("/").lower()

        if pattern == candidate:
            return True

        if pattern.startswith("*.") or "://*." in pattern:
            # Split into scheme and suffix, then require a label before the
            # suffix. A bare "*" must never match, so the subdomain has to be
            # present and non-empty.
            scheme, _, suffix = pattern.rpartition("://")
            if not suffix.startswith("*."):
                continue
            base = suffix[2:]
            prefix = f"{scheme}://" if scheme else ""
            if candidate.startswith(prefix) and candidate.endswith("." + base):
                middle = candidate[len(prefix) : -(len(base) + 1)]
                if middle and "." not in middle and "/" not in middle:
                    return True

    return False


class Config:
    """Base configuration from environment variables."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    DEBUG = os.environ.get("FLASK_ENV") == "development"
    TESTING = False

    # Database
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "sqlite:///sautipay.db",
    )

    SQLALCHEMY_ENGINE_OPTIONS = parse_pool_options(DATABASE_URL)

    # Model
    MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "mock")
    LOCAL_MODEL_PATH = os.environ.get("LOCAL_MODEL_PATH", "/models/sauti-model")
    LOCAL_MODEL_MAX_TOKENS = int(os.environ.get("LOCAL_MODEL_MAX_TOKENS", "1024"))
    LOCAL_MODEL_TEMPERATURE = float(os.environ.get("LOCAL_MODEL_TEMPERATURE", "0.7"))

    # Rate limiting
    RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
    RATE_LIMIT_STORAGE = os.environ.get("RATE_LIMIT_STORAGE", "memory://")

    # CORS. Parsed through parse_allowed_origins so a trailing slash or stray
    # whitespace cannot silently drop an origin. See the note there.
    ALLOWED_ORIGINS = parse_allowed_origins(
        os.environ.get("ALLOWED_ORIGINS", _DEV_ORIGINS)
    )

    # Logging
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # Admin content management
    ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

    # Media uploads
    MEDIA_ROOT = os.environ.get(
        "MEDIA_ROOT",
        str(Path(__file__).resolve().parent.parent / "instance" / "media"),
    )
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(50 * 1024 * 1024)))

    # SQL
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True
    TESTING = False
    ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-admin-token")


class TestingConfig(Config):
    """Testing configuration."""

    DEBUG = False
    TESTING = True
    DATABASE_URL = os.environ.get(
        "TEST_DATABASE_URL",
        "sqlite:///:memory:",
    )
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    # Derived from this class's own URL, not inherited. Config was built from
    # DATABASE_URL, which on a developer machine or CI is usually Postgres, so
    # inheriting its pool sizing would hand pool_size and max_overflow to a
    # SQLite StaticPool, which rejects them and fails every test at engine
    # creation.
    SQLALCHEMY_ENGINE_OPTIONS = parse_pool_options(DATABASE_URL)
    ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "test-admin-token")


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    TESTING = False

    # A public API is called from a browser, and a browser enforces CORS. With
    # ALLOWED_ORIGINS unset the base class falls back to localhost, so the
    # service boots, passes its health check, looks healthy, and then refuses
    # every real frontend with nothing but an opaque console error on the
    # client. Refusing to start turns that into a failed deploy with a message
    # naming the variable, which is far cheaper to diagnose.
    #
    # The check lives in validate_production(), not here. A class body is
    # executed at import time, so raising from it would abort `import
    # backend.config.base` for every environment, including local development.
    @classmethod
    def validate_production(cls) -> None:
        """Refuse to run production with unsafe configuration.

        Called from get_config() only when FLASK_ENV selects this class.
        """
        # Set it to a comma-separated list of exact origins, for example:
        #   ALLOWED_ORIGINS=https://app.example.com,https://www.example.com
        if not os.environ.get("ALLOWED_ORIGINS", "").strip():
            raise RuntimeError(
                "ALLOWED_ORIGINS is required in production. Without it every "
                "browser request is refused by CORS while the service still "
                "passes its health check. Set ALLOWED_ORIGINS to a "
                "comma-separated list of exact origins, e.g. "
                "https://app.example.com"
            )

        # Same reasoning for the session signing key. The base default is a
        # public constant, and anything Flask signs with it is forgeable.
        if (
            os.environ.get("SECRET_KEY", "dev-secret-change-me")
            == "dev-secret-change-me"
        ):
            raise RuntimeError(
                "SECRET_KEY is required in production and must not be the "
                "development default. Set it to a long random string."
            )


config_map = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": Config,
}


def get_config() -> type:
    """Return the appropriate config class based on FLASK_ENV.

    Production configuration is validated here rather than at import, so that
    a missing variable fails the deploy that needs it instead of breaking every
    local import of this module.
    """
    env = os.environ.get("FLASK_ENV", "default")
    config = config_map.get(env, Config)

    validate = getattr(config, "validate_production", None)
    if callable(validate):
        validate()

    return config