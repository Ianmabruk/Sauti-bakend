"""The planner decides whether a tool is needed, and which one.

Primary decision path: the model itself (when a live LLM is configured).
Fallback path: a deterministic heuristic in the research pipeline, so the
system still behaves correctly and safely with no credentials configured.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..config.settings import Settings
from ..services.llm import LLMService
from ..services.research.pipeline import build_search_query, needs_research
from ..agent.prompts import PLANNER_PROMPT
from ..agent.language import strip_language_request
from ..tools.base import ToolError

logger = logging.getLogger(__name__)

_VALID_TOOLS = {"web_search", "web_reader", "calculator"}


@dataclass
class ToolRequest:
    """A validated intent to call a tool."""

    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class Plan:
    """The planner's decision for one user turn."""

    needs_tool: bool
    reason: str
    tools: list[ToolRequest] = field(default_factory=list)
    decided_by: str = "heuristic"

    def to_dict(self) -> dict:
        return {
            "needs_tool": self.needs_tool,
            "reason": self.reason,
            "decided_by": self.decided_by,
            "tools": [{"name": t.name, "arguments": t.arguments} for t in self.tools],
        }


class Planner:
    """Chooses between answering directly and calling a tool."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        llm: Optional[LLMService] = None,
        available_tools: Optional[list[str]] = None,
    ):
        self.settings = settings or Settings()
        self.llm = llm or LLMService(self.settings)
        self.available_tools = available_tools or ["web_search", "web_reader", "calculator"]

    async def plan(
        self,
        message: str,
        language: str = "en",
        conversation_history: Optional[list[dict]] = None,
    ) -> Plan:
        """Decide what to do with a user message.

        Args:
            message: The raw user message.
            language: Detected language code.
            conversation_history: Prior turns, for context.

        Returns:
            A Plan. Never raises.
        """
        if self.llm.is_live:
            plan = await self._plan_with_model(message, conversation_history)
            if plan is not None:
                plan.decided_by = "model"
                return plan
            logger.info("PLANNER model plan unusable, falling back to heuristic")

        return self._plan_with_heuristic(message)

    # -- model path ------------------------------------------------------

    async def _plan_with_model(
        self, message: str, history: Optional[list[dict]]
    ) -> Optional[Plan]:
        tools_hint = ", ".join(sorted(self.available_tools))
        system = (
            f"{PLANNER_PROMPT}\n\n"
            f"Tools actually available in this session: {tools_hint}. "
            "You may only plan calls to these tools."
        )
        decision = await self.llm.complete_json(system, message, history)
        if not decision:
            return None

        needs_tool = bool(decision.get("needs_tool"))
        reason = str(decision.get("reason") or "")[:300]

        if not needs_tool:
            return Plan(needs_tool=False, reason=reason or "model judged no tool needed")

        requests: list[ToolRequest] = []
        for raw in decision.get("tools") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "")
            if name not in self.available_tools or name not in _VALID_TOOLS:
                logger.warning("PLANNER rejected unknown tool: %s", name)
                continue
            args = raw.get("arguments")
            requests.append(
                ToolRequest(name=name, arguments=args if isinstance(args, dict) else {})
            )

        if not requests:
            return Plan(needs_tool=False, reason="model requested no valid tool")

        return Plan(needs_tool=True, reason=reason or "model requested a tool", tools=requests)

    # -- heuristic path --------------------------------------------------

    def _plan_with_heuristic(self, message: str) -> Plan:
        from ..marketplace.query import is_discovery_request, is_news_request

        cleaned = strip_language_request(message)

        # Platform data first: if the user is looking for a vendor, product or
        # a price Sauti actually sells, the marketplace is authoritative.
        if is_discovery_request(cleaned):
            return Plan(
                needs_tool=True,
                reason="user is looking for a vendor or product; querying Sauti's own database",
                tools=[
                    ToolRequest(
                        name="search_marketplace",
                        arguments={"query": cleaned, "limit": 10},
                    )
                ],
            )

        # Sauti's own newsroom is the right source for published stories.
        if is_news_request(cleaned):
            return Plan(
                needs_tool=True,
                reason="news request; reading Sauti's published newsroom",
                tools=[ToolRequest(name="search_news", arguments={"query": cleaned, "limit": 6})],
            )

        required, reasons = needs_research(cleaned)

        if not required:
            return Plan(
                needs_tool=False,
                reason="no time-sensitive signals; answering from model knowledge",
            )

        query = build_search_query(cleaned)
        if not query:
            return Plan(needs_tool=False, reason="no searchable query could be built")

        return Plan(
            needs_tool=True,
            reason="time-sensitive request (" + ", ".join(reasons) + ")",
            tools=[ToolRequest(name="web_search", arguments={"query": query, "read_pages": True})],
        )


def coerce_request(name: str, arguments: dict) -> ToolRequest:
    """Build a ToolRequest, normalising argument types.

    Raises:
        ToolError: When the name or arguments are unusable.
    """
    if not isinstance(name, str) or not name.strip():
        raise ToolError("Tool name must be a non-empty string", tool_name=str(name))
    return ToolRequest(name=name.strip(), arguments=arguments if isinstance(arguments, dict) else {})
