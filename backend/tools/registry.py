"""Tool registry with schema validation and permission enforcement.

The registry is the only path from a model-authored tool request to real code
execution. It validates the request against the tool's input schema, checks the
tool's permission level, and converts any failure into a structured error the
agent can honestly report.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from ..config.settings import Settings
from .base import AUTO_EXECUTABLE, PermissionLevel, Tool, ToolError, ToolResult

logger = logging.getLogger(__name__)


class ToolNotFound(ToolError):
    """The requested tool is not registered."""


class ToolPermissionDenied(ToolError):
    """The tool exists but its permission level forbids auto-execution."""


def _model_from_schema(name: str, schema: dict) -> type[BaseModel]:
    """Build a Pydantic model from a JSON-schema-ish dict.

    Only the subset SAUTI needs is supported: typed fields with required
    flags, defaults and simple constraints.
    """
    type_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    fields: dict[str, Any] = {}
    properties: dict = schema.get("properties", {}) or {}
    required: set = set(schema.get("required", []) or [])

    for field_name, spec in properties.items():
        spec = spec or {}
        py_type = type_map.get(spec.get("type", "string"), str)

        kwargs: dict[str, Any] = {}
        if "default" in spec:
            kwargs["default"] = spec["default"]
        elif field_name in required:
            kwargs["default"] = ...
        else:
            kwargs["default"] = None

        if py_type is str and "maxLength" in spec:
            kwargs["max_length"] = int(spec["maxLength"])
        if py_type is str and "minLength" in spec:
            kwargs["min_length"] = int(spec["minLength"])
        if py_type in (int, float) and "minimum" in spec:
            kwargs["ge"] = spec["minimum"]
        if py_type in (int, float) and "maximum" in spec:
            kwargs["le"] = spec["maximum"]
        if py_type is list and "maxItems" in spec:
            kwargs["max_length"] = int(spec["maxItems"])

        annotation = Optional[py_type]
        fields[field_name] = (annotation, Field(**kwargs))

    model = create_model(f"{name}_input", __config__=ConfigDict(extra="forbid"), **fields)
    return model


class ToolRegistry:
    """Holds every available tool and mediates all execution."""

    def __init__(self, settings: Optional[Settings] = None):
        self._tools: dict[str, Tool] = {}
        self._models: dict[str, type[BaseModel]] = {}
        self._settings = settings

    def register(self, tool: Tool) -> None:
        """Register a tool. Raises on duplicates or malformed declarations."""
        if not tool.name:
            raise ValueError("Tool must declare a name")
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        if not isinstance(tool.input_schema, dict):
            raise ValueError(f"Tool '{tool.name}' must declare an input_schema dict")
        self._tools[tool.name] = tool
        self._models[tool.name] = _model_from_schema(tool.name, tool.input_schema)
        logger.info(
            "Registered tool=%s permission=%s network=%s",
            tool.name,
            tool.permission.value,
            tool.requires_network,
        )

    def register_all(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict]:
        """Machine-readable catalogue, useful for debugging and docs."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "permission": tool.permission.value,
                "input_schema": tool.input_schema,
                "output_schema": tool.output_schema,
                "requires_network": tool.requires_network,
            }
            for tool in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    def validate(self, name: str, arguments: Optional[dict]) -> dict:
        """Validate and normalise tool arguments.

        Args:
            name: Registered tool name.
            arguments: Raw arguments from the model.

        Returns:
            Normalised arguments.

        Raises:
            ToolNotFound: Unknown tool.
            ToolError: Arguments failed schema validation.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFound(f"Unknown tool: {name}", tool_name=name)

        model = self._models[name]
        payload = arguments if isinstance(arguments, dict) else {}
        try:
            validated = model(**payload)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}"
                for err in exc.errors()[:5]
            )
            raise ToolError(
                f"Invalid arguments for '{name}': {details}",
                tool_name=name,
            ) from exc
        return {k: v for k, v in validated.model_dump().items() if v is not None}

    async def execute(self, name: str, arguments: Optional[dict] = None) -> ToolResult:
        """Execute a registered tool after validation and permission checks.

        Never raises: every failure becomes a ToolResult with ok=False so the
        agent can report it truthfully instead of inventing a result.
        """
        started = time.perf_counter()

        try:
            tool = self._tools.get(name)
            if tool is None:
                raise ToolNotFound(f"Unknown tool: {name}", tool_name=name)

            settings = self._settings
            if tool.permission not in AUTO_EXECUTABLE:
                enabled = bool(getattr(settings, "enable_dangerous_tools", False)) if settings else False
                if not enabled:
                    raise ToolPermissionDenied(
                        f"Tool '{name}' requires {tool.permission.value} permission, "
                        "which is not enabled on this deployment.",
                        tool_name=name,
                    )

            clean_args = self.validate(name, arguments)
            result = await tool.run(clean_args)
        except ToolError as exc:
            return ToolResult(
                tool=name,
                ok=False,
                error=exc.message,
                duration_ms=int((time.perf_counter() - started) * 1000),
                metadata={"error_type": type(exc).__name__},
            )
        except Exception as exc:  # defensive: a tool must never crash the request
            logger.exception("Tool execution crashed: %s", name)
            return ToolResult(
                tool=name,
                ok=False,
                error=f"Tool '{name}' failed unexpectedly: {exc}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                metadata={"error_type": type(exc).__name__},
            )

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "TOOL %s status=%s duration_ms=%s",
            name,
            "ok" if result.ok else "error",
            result.duration_ms,
        )
        return result


def _settings_fingerprint(settings: Optional[Settings]) -> tuple:
    """Cheap identity for detecting that settings changed between calls."""
    s = settings or Settings()
    return (
        s.search_provider,
        bool(s.search_api_key),
        s.search_base_url,
        bool(s.llm_api_key),
        s.enable_dangerous_tools,
    )


def _default_tools(settings: Settings) -> list[Tool]:
    from ..marketplace.tools import (
        GetProductDetailsTool,
        GetVendorProfileTool,
        SearchMarketplaceTool,
        SearchNewsTool,
    )
    from .business_advisor import BusinessAdvisorTool
    from .calculator import CalculatorTool
    from .place_search import PlaceSearchTool
    from .source_links import SourceLinksTool
    from .study_generator import StudyGeneratorTool
    from .web_reader import WebReaderTool
    from .web_search import WebSearchTool

    return [
        # Platform data first: the model is told to prefer these.
        SearchMarketplaceTool(settings),
        GetVendorProfileTool(settings),
        GetProductDetailsTool(settings),
        SearchNewsTool(settings),
        # Real-world places and the links that make them navigable.
        PlaceSearchTool(settings),
        SourceLinksTool(settings),
        # Generated material, structured for the frontend cards.
        StudyGeneratorTool(settings),
        BusinessAdvisorTool(settings),
        # External / compute
        WebSearchTool(settings),
        WebReaderTool(settings),
        CalculatorTool(settings),
    ]


_registry: Optional[ToolRegistry] = None
_registry_key: Optional[tuple] = None


def get_registry(settings: Optional[Settings] = None) -> ToolRegistry:
    """Return the process-wide registry, rebuilding if settings changed.

    Rebuilding matters because tools capture settings at construction time.
    """
    global _registry, _registry_key
    key = _settings_fingerprint(settings)
    if _registry is None or _registry_key != key:
        effective = settings or Settings()
        _registry = ToolRegistry(effective)
        _registry.register_all(_default_tools(effective))
        _registry_key = key
    return _registry


def reset_registry(settings: Optional[Settings] = None) -> ToolRegistry:
    """Force a fresh registry. Used by tests."""
    global _registry, _registry_key
    effective = settings or Settings()
    _registry = ToolRegistry(effective)
    _registry.register_all(_default_tools(effective))
    _registry_key = _settings_fingerprint(effective)
    return _registry
