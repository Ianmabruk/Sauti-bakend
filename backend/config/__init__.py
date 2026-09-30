"""Configuration package for SautiPay / SAUTI.

`Config`, `TestingConfig` and friends live in :mod:`backend.config.base` and
are re-exported here so existing imports (``from backend.config import Config``)
keep working. Agent-specific settings live in :mod:`backend.config.settings`.
"""
from .base import (  # noqa: F401
    Config,
    DevelopmentConfig,
    ProductionConfig,
    TestingConfig,
    config_map,
    get_config,
)
from .settings import Settings, get_settings  # noqa: F401

__all__ = [
    "Config",
    "DevelopmentConfig",
    "ProductionConfig",
    "TestingConfig",
    "config_map",
    "get_config",
    "Settings",
    "get_settings",
]
