"""Tool catalogue API: inspect available tools and run one explicitly.

Running tools through this route still goes through the registry, so schema
validation and permission checks apply exactly as they do for the agent.
"""
from __future__ import annotations

import asyncio
import logging

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from ..config.settings import Settings
from ..schemas.tools import ToolCallRequest
from ..tools.registry import get_registry

logger = logging.getLogger(__name__)

tools_bp = Blueprint("tools", __name__)


def _registry():
    return get_registry(Settings())


@tools_bp.route("/tools", methods=["GET"])
def list_tools():
    """List every registered tool with its schema and permission level."""
    return jsonify({"tools": _registry().describe()})


@tools_bp.route("/tools/execute", methods=["POST"])
def execute_tool():
    """Execute a single registered tool.

    Body: {"name": "calculator", "arguments": {"expression": "2+2"}}
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be valid JSON"}), 400

    try:
        parsed = ToolCallRequest(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", ())) or "body"
        return jsonify({"error": f"{field}: {first.get('msg')}"}), 400

    result = asyncio.run(_registry().execute(parsed.name, parsed.arguments))
    return jsonify(result.to_dict()), (200 if result.ok else 400)
