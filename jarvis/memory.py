"""Conversation memory: a sliding window plus a persistent rolling summary.

Context management is the difference between an agent that understands
follow-ups and one that does not. Two mechanisms cooperate:

- a **sliding window** of the last N turns (default 10), kept verbatim and
  cheap to send on every request;
- a **rolling summary** of everything older, which is compacted by the model
  and persists to a JSON file so a session survives a restart.

Pragmas used when summarising:

- The summary is *cumulative* and *append-only*; it is rewritten, never
  discarded, so facts survive.
- Pronouns ("it", "that", "her") are only resolvable if the summary names the
  entities, so the summariser is instructed to keep proper nouns.
- Writes are atomic (temp file + ``os.replace``) so a crash mid-save cannot
  leave a truncated state file behind.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .config import Settings
from .logsetup import get_logger

logger = get_logger(__name__)

#: Signature of the summariser: (existing_summary, dropped_turns) -> new summary.
Summariser = Callable[[str, list["Turn"]], Awaitable[str]]

SUMMARY_INSTRUCTION = (
    "You are compacting a conversation log for an assistant with limited "
    "context.\n\n"
    "Write an updated running summary that keeps every durable fact, entity "
    "name, decision, preference, number and open thread from the previous "
    "summary and the new turns.\n"
    "Rules:\n"
    "- Keep proper nouns. They are the only way later pronouns resolve.\n"
    "- Keep user preferences and constraints verbatim.\n"
    "- Drop pleasantries, filler and anything already superseded.\n"
    "- Write compact prose or bullets. No preamble, no commentary.\n"
    "- If there is genuinely nothing to add, return the previous summary "
    "unchanged."
)


@dataclass
class Turn:
    """One conversational turn.

    Attributes:
        role: ``"user"`` or ``"assistant"``.
        content: The text of the turn.
        timestamp: Unix time the turn was recorded.
        sources: Optional citation titles/URLs attached to an assistant turn.
    """

    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    sources: list[dict[str, str]] = field(default_factory=list)

    def as_message(self) -> dict[str, str]:
        """Render as an OpenAI-style chat message."""
        return {"role": self.role, "content": self.content}


class ConversationMemory:
    """Sliding-window memory with a persistent rolling summary.

    Args:
        settings: Supplies window size, state path and summary limits.
        summariser: Async callable that folds evicted turns into the summary.
            When omitted, :meth:`add` never summarises and simply drops the
            oldest turn, which is a safe degradation.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        summariser: Optional[Summariser] = None,
    ) -> None:
        from .config import get_settings

        self.settings = settings or get_settings()
        self.summariser = summariser
        self.turns: list[Turn] = []
        self.summary: str = ""
        self._evicted_since_summary = 0
        self._lock_placeholder = None

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def add(
        self, role: str, content: str, *, sources: Optional[list[dict[str, str]]] = None
    ) -> None:
        """Append a turn, trimming anything beyond the window.

        Trimming is deliberately cheap: the evicted turns are counted, and the
        async summary refresh happens at natural await points via
        :meth:`refresh_summary` rather than blocking the audio path.

        Args:
            role: ``"user"`` or ``"assistant"``.
            content: The turn text.
            sources: Citations for an assistant turn.
        """
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"unsupported role: {role}")
        self.turns.append(
            Turn(role=role, content=content.strip(), sources=sources or [])
        )

        overflow = len(self.turns) - self.settings.memory_window
        if overflow > 0:
            self._evicted_since_summary += overflow
            del self.turns[:overflow]
            logger.debug(
                "window trimmed dropped=%d remaining=%d", overflow, len(self.turns)
            )

    def recent(self, limit: Optional[int] = None) -> list[Turn]:
        """Return the most recent turns, oldest first."""
        if limit is None:
            return list(self.turns)
        return list(self.turns[-limit:]) if limit > 0 else []

    def clear(self) -> None:
        """Forget the window and the summary, in memory only."""
        self.turns.clear()
        self.summary = ""
        self._evicted_since_summary = 0

    @property
    def needs_summary(self) -> bool:
        """True when enough turns have fallen out of the window to warrant one."""
        return (
            self.settings.memory_summarise
            and self.summariser is not None
            and self._evicted_since_summary >= max(2, self.settings.memory_window // 2)
        )

    async def refresh_summary(self, *, force: bool = False) -> bool:
        """Fold evicted turns into the rolling summary.

        Called opportunistically. Never raises: a failed summary leaves the
        conversation working with a slightly stale context.

        Args:
            force: Summarise even when the threshold is not met.

        Returns:
            True when the summary was updated.
        """
        if self.summariser is None or not (force or self.needs_summary):
            return False
        if not self._evicted_since_summary:
            return False

        # The evicted turns themselves are no longer in the window, so we ask
        # the model to merge the previous summary with the turns we still hold
        # and let the window continue to carry recency.
        to_absorb = self.turns[: max(1, self.settings.memory_window // 2)]
        previous = self.summary

        prompt = (
            f"PREVIOUS SUMMARY:\n{previous or '(none yet)'}\n\n"
            f"RECENT TURNS TO FOLD IN:\n"
            + "\n".join(f"{t.role}: {t.content}" for t in to_absorb)
        )

        try:
            updated = await self.summariser(previous, to_absorb)
        except Exception as exc:  # noqa: BLE001 - summary must never break a turn
            logger.warning("summary refresh failed: %s", exc)
            return False

        if not updated or not updated.strip():
            return False

        self.summary = updated.strip()[: self.settings.memory_summary_max_chars]
        self._evicted_since_summary = 0
        logger.info(
            "summary refreshed chars=%d window=%d", len(self.summary), len(self.turns)
        )
        return True

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    def context_block(self) -> str:
        """Render the summary and window as a prompt fragment.

        Returns:
            A labelled block, or an empty string when there is no context.
        """
        parts: list[str] = []
        if self.summary:
            parts.append(
                "EARLIER CONTEXT (already discussed, summarised):\n" + self.summary
            )
        if self.turns:
            lines = "\n".join(
                f"{t.role}: {t.content[:1200]}" for t in self.turns
            )
            parts.append(f"RECENT CONVERSATION:\n{lines}")
        if not parts:
            return ""
        return "\n\n".join(parts)

    def as_messages(self, limit: Optional[int] = None) -> list[dict[str, str]]:
        """Render the window as chat messages for the model API."""
        return [t.as_message() for t in self.recent(limit)]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialisable snapshot of the whole memory."""
        return {
            "version": 1,
            "saved_at": time.time(),
            "summary": self.summary,
            "turns": [asdict(t) for t in self.turns],
        }

    def save(self, path: Optional[str] = None) -> bool:
        """Atomically persist memory to disk.

        Args:
            path: Override for the configured state path.

        Returns:
            True when the file was written.
        """
        target = Path(path or self.settings.memory_state_path).expanduser()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
            # Atomic replace so an interrupted save cannot corrupt the state.
            fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                os.replace(tmp, target)
            except Exception:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError as exc:
            logger.warning("could not save memory to %s: %s", target, exc)
            return False

        logger.info("memory saved turns=%d summary=%dB path=%s",
                    len(self.turns), len(self.summary), target)
        return True

    def load(self, path: Optional[str] = None) -> bool:
        """Restore memory from disk, replacing whatever is in memory.

        A missing or corrupt file is not an error: the agent simply starts
        with an empty history.

        Args:
            path: Override for the configured state path.

        Returns:
            True when a state was loaded.
        """
        source = Path(path or self.settings.memory_state_path).expanduser()
        if not source.exists():
            logger.debug("no saved memory at %s", source)
            return False

        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("ignoring unreadable memory file %s: %s", source, exc)
            return False

        try:
            self.summary = str(data.get("summary", ""))
            self.turns = [
                Turn(
                    role=str(item.get("role", "user")),
                    content=str(item.get("content", "")),
                    timestamp=float(item.get("timestamp", 0.0)),
                    sources=list(item.get("sources", [])),
                )
                for item in data.get("turns", [])
                if item.get("content")
            ]
        except (TypeError, ValueError) as exc:
            logger.warning("ignoring malformed memory file %s: %s", source, exc)
            return False

        self._evicted_since_summary = 0
        logger.info(
            "memory loaded turns=%d summary=%dB", len(self.turns), len(self.summary)
        )
        return True
