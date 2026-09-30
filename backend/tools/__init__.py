"""SAUTI tool layer.

Every tool is registered in :mod:`backend.tools.registry` and must pass schema
validation and a permission check before it runs.
"""
from .base import (  # noqa: F401
    AUTO_EXECUTABLE,
    PermissionLevel,
    Tool,
    ToolError,
    ToolResult,
)
from .registry import (  # noqa: F401
    ToolNotFound,
    ToolPermissionDenied,
    ToolRegistry,
    get_registry,
    reset_registry,
)

__all__ = [
    "PermissionLevel",
    "Tool",
    "ToolError",
    "ToolResult",
    "AUTO_EXECUTABLE",
    "ToolRegistry",
    "ToolNotFound",
    "ToolPermissionDenied",
    "get_registry",
    "reset_registry",
]
