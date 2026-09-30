"""JARVIS persona, tool catalogue and the tool-routing contract.

This module is the single place where JARVIS's voice and its boundaries are
defined. It holds:

- the **persona**: witty, concise, addresses the user as "sir", never
  over-explains itself, never reads its own tool output aloud;
- the **tool catalogue** in the OpenAI function-calling schema shape that Groq
  and Ollama both accept, so routing is decided by the model rather than by
  keyword matching;
- the **rewriter prompt**, which turns "what's up with bitcoin" into
  "Bitcoin price USD today" before any search happens.
"""
from __future__ import annotations

from typing import Any, Optional

from .config import Settings

#: System prompt. Kept as plain text so it can be diffed and reviewed.
PERSONA = """\
You are JARVIS, a voice-first assistant. You are spoken to and you speak back.

VOICE
- You are being read aloud. Write for the ear, not the screen.
- One to three sentences by default. Expand only when the question genuinely \
needs it.
- No markdown, no bullet symbols, no emoji, no code fences. They sound like \
noise when spoken.
- Never say "sure", "certainly", "great question", "I'd be happy to", or \
"let me check for you". Just answer.
- You may address the user as "sir" occasionally. Sparingly, and never twice \
in a row.
- Numbers should be spoken naturally: say "three point five million", not \
"3,500,000".
- Read out only what matters. Never recite raw URLs, JSON, tool names or \
error codes.

CANDOUR
- If a search failed or returned nothing, say so plainly: "I couldn't verify \
that online, sir."
- Never state a fact you did not retrieve or already know with confidence.
- When sources disagree, present the disagreement instead of picking a value \
silently.
- If you are unsure, say what would make you sure.

MEMORY AND FOLLOW-UPS
- The conversation history you are given is real. Use it.
- Resolve pronouns ("it", "that", "her", "they") against the recent turns \
before searching. Never ask the user to repeat something they already told \
you.
- Treat the earlier-context summary as established fact about the user.

TOOLS
- You choose which tools to call. Do not guess a tool that is not listed.
- Call a search tool when the answer depends on anything that changes over \
time: prices, news, weather, scores, laws, availability, "today", "now", \
"latest".
- Answer directly from your own knowledge for stable facts: definitions, \
explanations, maths, how things work. Searching for those wastes the user's \
time.
- After calling a tool, answer from its result. Do not call the same tool \
twice for the same question.
"""

ROUTER_PROMPT = """\
You route one user message for a voice assistant.

Decide whether answering needs a live tool call, and if so, which one.

Answer directly (no tool) for stable knowledge: definitions, explanations, \
concepts, maths, writing, advice, code, and follow-ups that only need the \
conversation history.

Use "web_search" when the answer depends on changing information: current \
prices, news, weather, sports, exchange rates, laws, product availability, \
recent events, or anything phrased as "today", "now", "latest", "updating".

Respond with a single JSON object and nothing else:
{"needs_search": true, "query": "...", "reason": "..."}

- "query" is a rewritten search query, not the user's raw words. Expand \
colloquialisms, resolve "it"/"that" from the conversation, and add the words \
that make results precise (a date, a currency, a place, an entity).
- "query" must be a few keywords, not a full sentence.
- When no search is needed, "query" must be an empty string.
- Never call a tool for small talk, greetings, or thanks.

Example: "what's up with bitcoin" -> {"needs_search": true, "query": \
"Bitcoin price USD today", "reason": "current price"}
"""


#: OpenAI-style function-calling schemas. Groq and Ollama both accept this shape.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the live web for current information. Use for prices, "
                "news, weather, scores, laws, availability and anything that "
                "changes over time. Returns ranked sources with excerpts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Concise rewritten search query of 2-8 keywords.",
                    },
                    "objective": {
                        "type": "string",
                        "description": "One sentence describing what information is needed.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Fetch and extract the readable text of specific URLs when a "
                "search excerpt was not enough."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Absolute http/https URLs to extract.",
                    },
                    "objective": {
                        "type": "string",
                        "description": "What to look for on the page.",
                    },
                },
                "required": ["urls"],
            },
        },
    },
]


def build_system_prompt(
    settings: Settings, *, context: str = "", evidence: str = ""
) -> str:
    """Assemble the full system prompt for one turn.

    Args:
        settings: Supplies the configured form of address and window size.
        context: Rendered memory block from :class:`~jarvis.memory.ConversationMemory`.
        evidence: Formatted tool results, when any were gathered this turn.

    Returns:
        The complete system prompt.
    """
    parts = [PERSONA]

    if context:
        parts.append(context)

    if evidence:
        parts.append(
            "EVIDENCE RETRIEVED FOR THIS TURN\n"
            "This is the only fresh information you have. Base factual claims "
            "on it. Quote exact figures from it rather than from memory. If it "
            "is empty or shows a failure, say you could not verify the answer.\n\n"
            f"{evidence}"
        )

    parts.append(
        f"Speak naturally, referring to the user as '{settings.agent_user_name}' "
        "sparingly."
    )
    return "\n\n".join(parts)


def build_evidence_block(results: list[dict[str, Any]]) -> str:
    """Render tool results into a compact evidence block for the prompt.

    Args:
        results: Items of shape ``{"tool", "ok", "summary", "sources"}``.

    Returns:
        A text block, or an empty string when there is nothing to show.
    """
    if not results:
        return ""

    lines: list[str] = []
    for item in results:
        tool = item.get("tool", "tool")
        if not item.get("ok"):
            lines.append(f"[{tool}] FAILED: {item.get('summary', 'unknown error')}")
            continue

        lines.append(f"[{tool}] OK")
        summary = (item.get("summary") or "").strip()
        if summary:
            lines.append(summary)

        for source in (item.get("sources") or [])[:4]:
            title = (source.get("title") or "untitled").strip()
            url = (source.get("url") or "").strip()
            excerpt = " ".join((source.get("excerpt") or "").split())[:500]
            block = f"  - {title} <{url}>"
            if excerpt:
                block += f"\n    {excerpt}"
            lines.append(block)

    return "\n".join(lines).strip()


def build_router_prompt(message: str, context: str = "") -> str:
    """Build the user side of the routing request.

    Args:
        message: The user's current message.
        context: Recent conversation, so the router can resolve pronouns.

    Returns:
        A single string containing the message and any needed context.
    """
    parts = []
    if context:
        parts.append(f"RECENT CONVERSATION:\n{context[:2000]}")
    parts.append(f"USER MESSAGE:\n{message}")
    return "\n\n".join(parts)


def strip_citations(text: str) -> str:
    """Remove markdown and bracketed references before speaking.

    The model is told not to emit these, but text can still arrive with them
    from a fallback path or a summarised source, and they sound terrible
    spoken aloud.

    Args:
        text: Raw model output.

    Returns:
        Text safe for speech synthesis.
    """
    import re

    cleaned = re.sub(r"\[(\d+)\]", "", text)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"[*_`#>]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def format_citation_line(sources: list[dict[str, str]], limit: int = 2) -> str:
    """Build the spoken source attribution for factual answers.

    Args:
        sources: Items with ``title`` and ``url``.
        limit: How many sources to name. Two keeps it short for the ear.

    Returns:
        A sentence to append to the answer, or an empty string.
    """
    picked = [s for s in sources if s.get("url")][:limit]
    if not picked:
        return ""
    titles = [s.get("title") or s.get("url", "") for s in picked]
    if len(titles) == 1:
        return f" Source: {titles[0]}."
    return f" Sources: {titles[0]} and {titles[1]}."


def json_only(text: str) -> Optional[dict]:
    """Extract the first JSON object from a model response.

    The router is asked for bare JSON but models still wrap it in prose or
    fences often enough to justify a tolerant reader.

    Args:
        text: Raw model output.

    Returns:
        The parsed object, or None when nothing parses.
    """
    import json
    import re

    if not text:
        return None

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text

    start = candidate.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(candidate[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None
