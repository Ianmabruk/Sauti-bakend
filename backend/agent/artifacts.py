"""Persistence for generated study material and business plans.

Why this exists: a student asking for "harder questions" a few minutes after
asking for revision questions is still working on the same material. Without
persistence the agent regenerates from scratch and the earlier work is lost.

What is stored is **what the agent generated**, at the user's explicit request,
never anything the user volunteered about themselves. The long-term memory
policy in :mod:`backend.memory.service` deliberately refuses to store user
statements automatically; this module is the narrow exception for the agent's
own output, and it follows the same conservative rules:

- only these two artifact kinds are ever stored, never free-form user text;
- nothing is stored unless a study or business tool actually succeeded;
- a failed or empty generation is never persisted, so a later recall cannot
  surface material that does not exist;
- content is truncated before it is written, so one enormous response cannot
  bloat the database.

Storage reuses the existing ``memory_items`` table with
``category="project"`` and structured ``extra_metadata``, so no migration is
needed and the data is retrievable through the existing recall path.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Category used for generated artifacts.
CATEGORY = "project"

#: Tool name to artifact kind.
_ARTIFACT_TOOLS = {
    "study_generator": "study",
    "business_advisor": "business",
}

#: Generous but bounded: enough to rebuild a study session, small enough that
#: recalling it does not crowd out the real memory budget.
_MAX_CONTENT_CHARS = 6000

#: Sorts rows that carry no timestamp to the end rather than raising.
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def artifact_kind(tool_name: str) -> Optional[str]:
    """Which kind of artifact a tool produces, if any.

    Args:
        tool_name: Name of the tool that ran.

    Returns:
        ``"study"``, ``"business"``, or None when the tool produces no
        storable artifact.
    """
    return _ARTIFACT_TOOLS.get(tool_name)


def _summary_line(kind: str, data: dict) -> str:
    """One line describing an artifact, for injection into the prompt."""
    if kind == "study":
        topic = data.get("topic") or "a topic"
        level = data.get("level") or "unspecified level"
        questions = len(data.get("practiceQuestions") or [])
        return (
            f"study material on '{topic}' ({level}) with {questions} practice "
            f"question(s)"
        )
    business = data.get("business") or "a business"
    goal = data.get("goal") or "unspecified goal"
    return f"business plan for '{business}' with the goal: {goal}"


class ArtifactStore:
    """Saves and recalls generated study and business artifacts.

    Args:
        memory_service: A :class:`~backend.memory.service.MemoryService`.
            Injected rather than constructed so this can be exercised without
            a database.
    """

    def __init__(self, memory_service):
        self.memory = memory_service

    # -- write ----------------------------------------------------------

    def record_from_result(
        self,
        result: Any,
        *,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Optional[dict]:
        """Persist an artifact from a tool result, if it is storable.

        Args:
            result: A :class:`~backend.tools.base.ToolResult`.
            user_id: Optional owner, for scoping recalls.
            conversation_id: The conversation it belongs to.
            language: Reply language, recorded alongside the artifact.

        Returns:
            The stored metadata dict, or None when nothing was stored.
        """
        kind = artifact_kind(getattr(result, "tool", "") or "")
        if kind is None or not getattr(result, "ok", False):
            return None

        data = getattr(result, "data", None)
        if not isinstance(data, dict) or not data:
            return None

        # A study card needs a topic and a business plan needs a name, so an
        # empty or half-built generation is never stored.
        anchor = data.get("topic") if kind == "study" else data.get("business")
        if not anchor:
            logger.info("artifact not stored: %s had no subject", kind)
            return None

        summary = _summary_line(kind, data)
        try:
            content = json.dumps(data, default=str)[:_MAX_CONTENT_CHARS]
        except (TypeError, ValueError) as exc:
            logger.warning("artifact not serialisable: %s", exc)
            return None

        metadata = {
            "artifact": kind,
            "subject": str(anchor)[:200],
            "summary": summary,
            "questionCount": len(data.get("practiceQuestions") or []) or None,
        }

        try:
            item = self.memory.remember(
                content=content,
                category=CATEGORY,
                user_id=user_id,
                conversation_id=conversation_id,
                language=language,
                importance=0.6,
                # Without this the stored row carries no marker saying it is a
                # generated artifact, so recall would treat it as an ordinary
                # memory and never surface it as follow-up context.
                extra_metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 - persistence is never fatal
            # A rejected or failed write must not break the turn the student
            # just asked for. The answer is still delivered either way.
            logger.warning("artifact not persisted: %s", exc)
            return None

        logger.info("artifact stored kind=%s subject=%r", kind, metadata["subject"])
        return metadata

    # -- read -----------------------------------------------------------

    def latest_for_mode(
        self, kind: str, *, user_id: Optional[str] = None, limit: int = 5
    ) -> list[str]:
        """Return prompt lines for the most recent artifacts of one kind.

        This is the lookup a bare follow-up needs. "Make them harder" shares
        no words with "Human Anatomy", so a relevance search finds nothing,
        yet the student plainly means the material generated moments ago. When
        a turn is in education or business mode, the newest artifact of that
        kind is the right context regardless of wording.

        Args:
            kind: ``"study"`` or ``"business"``.
            user_id: Optional owner scoping.
            limit: How many summaries to return at most.

        Returns:
            Short prompt lines, newest first. Empty when nothing is stored.
        """
        if kind not in {"study", "business"}:
            return []
        try:
            items = self.memory.list_all(user_id=user_id, category=CATEGORY)
        except Exception as exc:  # noqa: BLE001
            logger.warning("artifact listing failed: %s", exc)
            return []

        # Sort here rather than trusting the caller's ordering. `list_all`
        # returns rows in whatever order the query used, and the whole point of
        # this lookup is "the one generated moments ago". Items without a
        # usable timestamp keep their incoming order.
        ordered = sorted(
            items,
            key=lambda item: getattr(item, "created_at", None) or _EPOCH,
            reverse=True,
        )

        lines: list[str] = []
        for item in ordered:
            metadata = getattr(item, "extra_metadata", None) or {}
            if metadata.get("artifact") != kind:
                continue
            lines.append(
                metadata.get("summary") or f"a saved {kind} artifact"
            )
            if len(lines) >= limit:
                break
        return lines

    def recall_lines(
        self,
        query: str,
        *,
        user_id: Optional[str] = None,
        limit: int = 3,
    ) -> list[str]:
        """Return prompt lines for artifacts relevant to a query.

        Args:
            query: The user's current message.
            user_id: Optional owner scoping.
            limit: How many artifacts to mention at most.

        Returns:
            Short lines for the system prompt. Empty when nothing matches, so
            an unrelated question does not drag old material into context.
        """
        if not query or not (query or "").strip():
            return []
        try:
            items = self.memory.recall(query, user_id=user_id, limit=limit * 3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("artifact recall failed: %s", exc)
            return []

        lines: list[str] = []
        for item in items:
            metadata = getattr(item, "extra_metadata", None) or {}
            if metadata.get("artifact") not in {"study", "business"}:
                continue
            lines.append(
                metadata.get("summary")
                or f"a {metadata.get('artifact', 'saved')} artifact"
            )
            if len(lines) >= limit:
                break
        return lines

    def load_artifact(self, subject: str, *, user_id: Optional[str] = None):
        """Load a stored artifact by subject, most recent first.

        Args:
            subject: Topic or business name to match, case-insensitively.
            user_id: Optional owner scoping.

        Returns:
            ``(kind, data)`` for the newest match, or ``(None, None)``.
        """
        if not subject or not subject.strip():
            return None, None
        try:
            items = self.memory.list_all(user_id=user_id, category=CATEGORY)
        except Exception as exc:  # noqa: BLE001
            logger.warning("artifact load failed: %s", exc)
            return None, None

        needle = subject.strip().lower()
        for item in items:
            metadata = getattr(item, "extra_metadata", None) or {}
            stored = str(metadata.get("subject") or "").lower()
            if stored and (stored == needle or needle in stored or stored in needle):
                try:
                    return metadata.get("artifact"), json.loads(item.content)
                except (json.JSONDecodeError, TypeError):
                    return metadata.get("artifact"), None
        return None, None
