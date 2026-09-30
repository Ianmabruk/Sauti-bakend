"""Data access for long-term memory.

Isolated behind a repository so the storage backend can be replaced without
touching the agent.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import or_

from ..db import db
from .models import MEMORY_CATEGORIES, MemoryItem

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)

# Words that carry no retrieval signal. Without this, a question such as
# "What is 2 + 2? Reply with just the number." contributes the term "the",
# which matches almost every stored document and pulls unrelated memories into
# the prompt. Kept deliberately small: the matcher is lexical, so a long list
# would start discarding meaningful queries.
_STOP_WORDS = frozenset(
    {
        "about", "after", "again", "against", "all", "also", "and", "any", "are",
        "because", "been", "before", "being", "both", "but", "can", "could", "did",
        "does", "doing", "done", "down", "during", "each", "few", "for", "from",
        "further", "give", "had", "has", "have", "having", "her", "here", "hers",
        "him", "his", "how", "into", "its", "just", "like", "make", "many", "more",
        "most", "much", "must", "need", "not", "now", "off", "once", "only", "or",
        "other", "our", "ours", "out", "over", "own", "please", "question", "reply",
        "same", "she", "should", "some", "such", "tell", "than", "that", "the",
        "their", "theirs", "them", "then", "there", "these", "they", "this", "those",
        "through", "too", "under", "until", "use", "very", "want", "was", "way",
        "well", "were", "what", "when", "where", "which", "while", "who", "whom",
        "why", "will", "with", "would", "you", "your", "yours",
    }
)


def _query_terms(query: str) -> set[str]:
    """Distinct, meaningful, lowercased terms from a query."""
    return {
        term
        for term in (t.lower() for t in _WORD_RE.findall(query or ""))
        if len(term) > 2 and term not in _STOP_WORDS
    }


class MemoryRepository:
    """CRUD and retrieval for memory items."""

    def create(
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
        """Create a memory item.

        Raises:
            ValueError: On empty content or an unknown category.
        """
        text = (content or "").strip()
        if not text:
            raise ValueError("Memory content cannot be empty")
        if category not in MEMORY_CATEGORIES:
            raise ValueError(
                f"Unknown memory category '{category}'. Allowed: {MEMORY_CATEGORIES}"
            )

        item = MemoryItem(
            content=text,
            category=category,
            user_id=user_id,
            conversation_id=conversation_id,
            key=(key or "").strip()[:160] or None,
            language=language,
            importance=max(0.0, min(1.0, float(importance))),
            extra_metadata=extra_metadata or None,
        )
        db.session.add(item)
        db.session.commit()
        logger.info("MEMORY created id=%s category=%s", item.id, item.category)
        return item

    def upsert(
        self, key: str, content: str, category: str = "preference", user_id: Optional[str] = None
    ) -> MemoryItem:
        """Create or update a keyed memory, e.g. a language preference."""
        existing = self.get_by_key(key, user_id=user_id)
        if existing:
            existing.content = content.strip()
            existing.category = category
            existing.is_active = True
            db.session.commit()
            return existing
        return self.create(
            content=content, category=category, user_id=user_id, key=key,
            importance=0.8,
        )

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return db.session.get(MemoryItem, item_id)

    def get_by_key(self, key: str, user_id: Optional[str] = None) -> Optional[MemoryItem]:
        query = MemoryItem.query.filter_by(key=key, is_active=True)
        if user_id:
            query = query.filter_by(user_id=user_id)
        return query.first()

    def list_all(
        self,
        user_id: Optional[str] = None,
        category: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 100,
    ) -> list[MemoryItem]:
        query = MemoryItem.query
        if user_id:
            query = query.filter_by(user_id=user_id)
        if category:
            query = query.filter_by(category=category)
        if not include_inactive:
            query = query.filter_by(is_active=True)
        return query.order_by(MemoryItem.importance.desc(), MemoryItem.created_at.desc()).limit(limit).all()

    def recall(self, query: str, user_id: Optional[str] = None, limit: int = 5) -> list[MemoryItem]:
        """Retrieve memories relevant to a query.

        Scoring is a deterministic lexical overlap so it is testable. The
        user's language preference is always included because it governs every
        reply, not just replies that mention language.

        A vector store can replace this later without changing the interface.
        """
        candidates = self.list_all(user_id=user_id, limit=500)
        if not candidates:
            return []

        always: list[MemoryItem] = []
        scored: list[tuple[float, MemoryItem]] = []

        terms = _query_terms(query)
        query_lower = (query or "").strip().lower()

        for item in candidates:
            # Language preference applies to every turn.
            if item.key and item.key.lower() in {"language", "reply_language"}:
                always.append(item)
                continue

            haystack = f"{item.key or ''} {item.category} {item.content}".lower()
            if terms:
                # Whole-word overlap. A substring test would let "the" match
                # "thermodynamics", which is what made unrelated memories
                # score above zero.
                words = {t.lower() for t in _WORD_RE.findall(haystack)}
                overlap = len(terms & words)
                if overlap:
                    score = (overlap / len(terms)) * 0.7 + item.importance * 0.3
                    scored.append((score, item))
            elif query_lower and query_lower in haystack:
                # Nothing survived stop-word removal, so fall back to matching
                # the whole phrase rather than guessing.
                scored.append((item.importance, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        combined = always + [item for _, item in scored]
        return combined[:limit]

    def delete(self, item_id: str) -> bool:
        item = self.get(item_id)
        if not item:
            return False
        db.session.delete(item)
        db.session.commit()
        logger.info("MEMORY deleted id=%s", item_id)
        return True

    def forget(self, key: str, user_id: Optional[str] = None) -> int:
        """Deactivate all memories matching a key. Returns the count removed."""
        query = MemoryItem.query.filter(
            or_(MemoryItem.key == key, MemoryItem.content.ilike(f"%{key}%"))
        ).filter_by(is_active=True)
        if user_id:
            query = query.filter_by(user_id=user_id)
        items = query.all()
        for item in items:
            item.is_active = False
        db.session.commit()
        logger.info("MEMORY forgot key=%s count=%s", key, len(items))
        return len(items)

    def touch_all(self, items: list[MemoryItem]) -> None:
        for item in items:
            item.touch()
        db.session.commit()
