"""Memory service: the agent-facing API for long-term memory.

Policy is deliberately conservative:
- nothing is stored automatically from a chat turn
- a caller must explicitly ask to remember something
- categories that would hold credentials or financial account data are refused
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..config.settings import Settings, get_settings
from .models import FORBIDDEN_CATEGORIES, MEMORY_CATEGORIES, MemoryItem
from .repository import MemoryRepository

logger = logging.getLogger(__name__)

#: Patterns that suggest the caller is trying to store a secret.
_SENSITIVE_PATTERNS = (
    re.compile(r"\b(api[_ -]?key|secret|password|passcode|pin\b|token|private key|credential)\b", re.I),
    re.compile(r"\b(account number|sort code|ifsc|bank account|card number|cvv)\b", re.I),
    re.compile(r"\b\d{6,}\b"),  # long digit runs: card/account/id numbers
)

#: Phrases that mean "the user is stating a durable preference".
_PREFERENCE_PATTERNS = (
    re.compile(r"\b(?:my|my) (?:preferred|favourite|favorite) (\w+) is ([^\n.]+)", re.I),
    re.compile(r"\bi (?:prefer|like|always want) ([^\n.]+)", re.I),
    re.compile(r"\b(?:remember|note) that ([^\n.]+)", re.I),
    re.compile(r"\b(?:na) upendelea ([^\n.]+)", re.I),   # Kiswahili
    re.compile(r"\b(?:je pr(?:é|e)fère|m\xe9morise que) ([^\n.]+)", re.I),  # French
)


class MemoryRejected(ValueError):
    """The requested memory was refused by policy."""


class MemoryService:
    """Create, recall and forget long-term memories."""

    def __init__(self, settings: Optional[Settings] = None, repository=None):
        self.settings = settings or get_settings()
        self.repository = repository or MemoryRepository()

    # -- policy ---------------------------------------------------------

    def check_allowed(self, content: str, category: str = "context") -> None:
        """Raise MemoryRejected when the content must not be stored."""
        if category in FORBIDDEN_CATEGORIES:
            raise MemoryRejected(
                f"Refusing to store memory of category '{category}'."
            )
        for pattern in _SENSITIVE_PATTERNS:
            if pattern.search(content or ""):
                raise MemoryRejected(
                    "Refusing to store this: it looks like it contains a "
                    "credential or sensitive account detail."
                )

    # -- write ----------------------------------------------------------

    def remember(
        self,
        content: str,
        category: str = "context",
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        key: Optional[str] = None,
        language: Optional[str] = None,
        importance: float = 0.5,
        extra_metadata: Optional[dict] = None,
    ) -> MemoryItem:
        """Store a memory after policy checks.

        Args:
            content: The text to store.
            category: One of :data:`MEMORY_CATEGORIES`.
            user_id: Optional owner.
            conversation_id: Optional owning conversation.
            key: Optional dedupe key.
            language: Optional language tag.
            importance: Relevance weight used when ranking recall.
            extra_metadata: Optional structured detail. Must be JSON
                serialisable; it is stored alongside the content so a
                caller can find a specific memory without re-parsing
                every one of them.

        Raises:
            MemoryRejected: If the content looks sensitive.
            ValueError: If the content is empty or the category unknown.
        """
        if not self.settings.memory_enabled:
            raise MemoryRejected("Memory is disabled on this deployment.")
        self.check_allowed(content, category)
        return self.repository.create(
            content=content,
            category=category,
            user_id=user_id,
            conversation_id=conversation_id,
            key=key,
            language=language,
            importance=importance,
            extra_metadata=extra_metadata,
        )

    def remember_preference(
        self, key: str, value: str, user_id: Optional[str] = None
    ) -> MemoryItem:
        """Store or update a keyed preference such as a language."""
        self.check_allowed(value, "preference")
        return self.repository.upsert(key=key, content=value, category="preference", user_id=user_id)

    def get_preference(self, key: str, user_id: Optional[str] = None) -> Optional[str]:
        item = self.repository.get_by_key(key, user_id=user_id)
        return item.content if item else None

    # -- read -----------------------------------------------------------

    def recall(
        self, query: str, user_id: Optional[str] = None, limit: Optional[int] = None
    ) -> list[MemoryItem]:
        """Return memories relevant to a query, newest-usefulness first."""
        if not self.settings.memory_enabled:
            return []
        cap = limit or self.settings.memory_recall_limit
        items = self.repository.recall(query, user_id=user_id, limit=cap)
        if items:
            self.repository.touch_all(items)
        return items

    def recall_as_prompt_lines(self, query: str, user_id: Optional[str] = None) -> list[str]:
        """Memories formatted for injection into the system prompt.

        Each line is truncated to ``memory_max_content_chars``. A single
        stored item can be a full study set or business plan of several
        kilobytes, and injecting it whole crowds out the question and the
        rest of the system prompt. The omitted length is reported so the model
        knows the memory is only a summary.
        """
        cap = max(0, self.settings.memory_max_content_chars)
        lines: list[str] = []
        for item in self.recall(query, user_id):
            content = item.content or ""
            if cap and len(content) > cap:
                omitted = len(content) - cap
                content = f"{content[:cap].rstrip()}… [+{omitted} chars omitted]"
            lines.append(f"[{item.category}] {content}")
        return lines

    def list_all(self, user_id: Optional[str] = None, category: Optional[str] = None) -> list[MemoryItem]:
        return self.repository.list_all(user_id=user_id, category=category)

    # -- delete ---------------------------------------------------------

    def forget(self, item_id: str) -> bool:
        return self.repository.delete(item_id)

    def forget_matching(self, key: str, user_id: Optional[str] = None) -> int:
        return self.repository.forget(key, user_id=user_id)

    # -- passive detection ----------------------------------------------

    def detect_preference_statement(self, text: str) -> Optional[dict]:
        """Spot an explicit 'my preferred X is Y' statement.

        This does NOT store anything. The caller decides whether to persist.
        It exists so the API can offer a one-click 'remember this' path.

        Returns:
            {"key": ..., "value": ...} or None.
        """
        for pattern in _PREFERENCE_PATTERNS:
            match = pattern.search(text or "")
            if not match:
                continue
            if match.lastindex == 2:
                key, value = match.group(1), match.group(2)
            else:
                key, value = "preference", match.group(1)
            value = value.strip().rstrip(".!")
            if value:
                return {"key": key.strip().lower(), "value": value}
        return None
