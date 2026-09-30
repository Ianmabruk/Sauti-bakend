"""Structured business advice for Kenyan small businesses.

Produces a business plan as structured data so the frontend can render a
business card: target customers, marketing, pricing, a 7-day action plan and a
30-day plan.

The prompt is deliberately Kenya-specific. Generic advice that ignores M-Pesa,
county market structure or shilling pricing is exactly the failure this tool
exists to avoid, so the country and city are injected from settings.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ..config.settings import Settings
from .base import PermissionLevel, Tool, ToolResult

logger = logging.getLogger(__name__)

_BUSINESS_PROMPT = """\
Give practical, specific advice for a small business in {country}, based in {city}.

Business: {business}
Goal: {goal}
{stage_line}

Return ONLY a JSON object with this exact shape:
{{
  "business": "...",
  "location": "{city}, {country}",
  "goal": "...",
  "targetCustomers": ["a specific group, with why they would buy"],
  "marketing": ["concrete steps, not slogans"],
  "pricing": ["how to price, with shilling figures where it helps"],
  "sevenDayPlan": [{{"day": "Day 1", "action": "..."}}],
  "thirtyDayPlan": ["the next milestones after week one"]
}}

Rules:
- Be specific to this business. No generic advice that would fit any shop.
- Prefer what actually works for a small Kenyan operation: M-Pesa, WhatsApp
  groups, roadside visibility, local market days, referrals.
- Money must be in Kenyan shillings (KSh).
- "sevenDayPlan" must have exactly 7 entries.
- Do not invent statistics, survey results or market sizes. If a figure is
  unknown, describe how to find it out.
- Write in {language}.
"""

#: What a business is trying to achieve, mapped to a short stage hint. The
#: model gets this because the same question needs different depth depending
#: on whether the business exists yet.
_STAGE_HINTS = {
    "starting": "This business does not exist yet. Cover setup and first customers.",
    "growing": "This business is trading and wants more customers.",
    "trouble": "This business is trading but sales are slow or falling.",
    "scaling": "This business works and wants to expand to more locations or products.",
}


class BusinessAdvisorTool(Tool):
    """Generate a practical, Kenya-specific plan for a small business."""

    name = "business_advisor"
    description = (
        "Give practical business advice for a small Kenyan business: target "
        "customers, marketing, pricing, a 7-day action plan and a 30-day plan. "
        "Use this when a user asks how to start, grow, market or rescue a "
        "business or shop."
    )
    permission = PermissionLevel.SAFE
    requires_network = False

    input_schema = {
        "type": "object",
        "properties": {
            "business": {
                "type": "string",
                "minLength": 2,
                "maxLength": 160,
                "description": "What the business sells, e.g. 'cereal shop'.",
            },
            "goal": {
                "type": "string",
                "maxLength": 200,
                "description": "What the owner wants, e.g. 'more customers'.",
            },
            "stage": {
                "type": "string",
                "enum": sorted(_STAGE_HINTS),
                "default": "starting",
                "description": "Where the business is right now.",
            },
            "language": {
                "type": "string",
                "maxLength": 10,
                "default": "en",
            },
        },
        "required": ["business"],
    }

    output_schema = {
        "type": "object",
        "properties": {
            "business": {"type": "string"},
            "location": {"type": "string"},
            "goal": {"type": "string"},
            "targetCustomers": {"type": "array"},
            "marketing": {"type": "array"},
            "pricing": {"type": "array"},
            "sevenDayPlan": {"type": "array"},
            "thirtyDayPlan": {"type": "array"},
        },
    }

    def __init__(self, settings: Optional[Settings] = None):
        from ..config.settings import get_settings

        self.settings = settings or get_settings()

    async def run(self, arguments: dict) -> ToolResult:
        """Generate a business plan.

        Args:
            arguments: ``business`` and optional ``goal``, ``stage`` and
                ``language``.

        Returns:
            A ToolResult whose ``data`` is the structured plan, or a failure
            when the model is unreachable.
        """
        business = (arguments.get("business") or "").strip()
        if not business:
            return ToolResult(tool=self.name, ok=False, error="business is required")

        goal = (arguments.get("goal") or "grow the business").strip()
        stage = str(arguments.get("stage") or "starting").lower()
        if stage not in _STAGE_HINTS:
            stage = "starting"
        language = (arguments.get("language") or "en").strip() or "en"

        prompt = _BUSINESS_PROMPT.format(
            business=business,
            goal=goal,
            country=self.settings.user_country,
            city=self.settings.user_city,
            stage_line=_STAGE_HINTS[stage],
            language=language,
        )

        from ..integrations.groq import GroqClient, GroqError

        client = GroqClient(self.settings)
        if not client.configured:
            return ToolResult(
                tool=self.name,
                ok=False,
                error="No model is configured to give business advice.",
            )

        try:
            raw = await client.complete_text(prompt, temperature=0.5, max_tokens=2600)
        except GroqError as exc:
            logger.warning("business_advisor failed: %s", exc.status)
            return ToolResult(
                tool=self.name,
                ok=False,
                error="Business advice could not be generated right now. Please try again.",
            )

        plan = _parse_plan(raw)
        if plan is None:
            return ToolResult(
                tool=self.name,
                ok=False,
                error="Business advice could not be generated right now. Please try again.",
            )

        plan.setdefault("business", business)
        plan.setdefault("goal", goal)
        plan.setdefault("location", f"{self.settings.user_city}, {self.settings.user_country}")
        plan["stage"] = stage
        logger.info("business_advisor business=%r stage=%s", business, stage)
        return ToolResult(tool=self.name, ok=True, data=plan)


def _parse_plan(raw: str) -> Optional[dict[str, Any]]:
    """Extract the plan object from a model reply, or return None."""
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("business_advisor returned invalid JSON")
        return None
    if not isinstance(payload, dict):
        return None
    return payload
