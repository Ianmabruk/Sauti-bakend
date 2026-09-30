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

    # Model
    MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "mock")
    LOCAL_MODEL_PATH = os.environ.get("LOCAL_MODEL_PATH", "/models/sauti-model")
    LOCAL_MODEL_MAX_TOKENS = int(os.environ.get("LOCAL_MODEL_MAX_TOKENS", "1024"))
    LOCAL_MODEL_TEMPERATURE = float(os.environ.get("LOCAL_MODEL_TEMPERATURE", "0.7"))

    # Rate limiting
    RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
    RATE_LIMIT_STORAGE = os.environ.get("RATE_LIMIT_STORAGE", "memory://")

    # CORS
    ALLOWED_ORIGINS = [
        o.strip()
        for o in os.environ.get(
            "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000"
        ).split(",")
    ]

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
    ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "test-admin-token")


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    TESTING = False


config_map = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": Config,
}


def get_config() -> type:
    """Return the appropriate config class based on FLASK_ENV."""
    env = os.environ.get("FLASK_ENV", "default")
    return config_map.get(env, Config)