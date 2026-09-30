"""calculator tool: safe arithmetic.

Uses an AST allowlist, never ``eval``/``exec``, so a model-authored expression
can never execute arbitrary code.
"""
from __future__ import annotations

import ast
import logging
import math
import operator
from typing import Any, Callable

from ..config.settings import Settings
from .base import PermissionLevel, Tool, ToolResult

logger = logging.getLogger(__name__)

_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "pow": math.pow,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
}

_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}

#: Refuse absurdly large exponents so a model cannot wedge the process.
_MAX_EXPONENT = 1024


class CalculatorError(ValueError):
    """The expression could not be safely evaluated."""


def safe_eval(expression: str) -> float:
    """Evaluate an arithmetic expression with a strict AST allowlist.

    Args:
        expression: e.g. "(3400 * 3) / 90"

    Returns:
        The numeric result.

    Raises:
        CalculatorError: On disallowed syntax or an undefined name.
    """
    cleaned = expression.strip()
    if not cleaned:
        raise CalculatorError("Empty expression")
    if len(cleaned) > 200:
        raise CalculatorError("Expression is too long")

    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"Not a valid expression: {exc.msg}") from exc

    return _eval_node(tree.body)


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("Only numbers are allowed")
        return node.value

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"Operator not allowed: {type(node.op).__name__}")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise CalculatorError("Exponent is too large")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise CalculatorError("Division by zero")
        return op(left, right)

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"Unary operator not allowed: {type(node.op).__name__}")
        return op(_eval_node(node.operand))

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise CalculatorError(f"Unknown name: {node.id}")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise CalculatorError("Only whitelisted math functions may be called")
        args = [_eval_node(a) for a in node.args]
        try:
            return _FUNCTIONS[node.func.id](*args)
        except Exception as exc:  # noqa: BLE001
            raise CalculatorError(str(exc)) from exc

    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_node(e) for e in node.elts]

    raise CalculatorError(f"Unsupported expression element: {type(node).__name__}")


class CalculatorTool(Tool):
    """Evaluate a safe arithmetic expression."""

    name = "calculator"
    description = (
        "Evaluate an arithmetic expression exactly. Use this for any "
        "calculation rather than doing mental arithmetic."
    )
    permission = PermissionLevel.COMPUTE
    requires_network = False

    input_schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "Arithmetic expression, e.g. '(3400 * 3) / 90'.",
            }
        },
        "required": ["expression"],
    }

    output_schema = {
        "type": "object",
        "properties": {"result": {"type": "number"}, "expression": {"type": "string"}},
    }

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    async def run(self, arguments: dict) -> ToolResult:
        expression = (arguments.get("expression") or "").strip()
        if not expression:
            return ToolResult(tool=self.name, ok=False, error="expression is required")
        try:
            value = safe_eval(expression)
        except CalculatorError as exc:
            return ToolResult(tool=self.name, ok=False, error=str(exc))
        return ToolResult(
            tool=self.name,
            ok=True,
            data={"expression": expression, "result": value},
        )
