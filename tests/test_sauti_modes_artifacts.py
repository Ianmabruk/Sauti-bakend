"""Tests for Phase 6: mode-aware prompts and artifact persistence."""
from __future__ import annotations

import json

import pytest

from backend.agent.artifacts import ArtifactStore, artifact_kind
from backend.agent.prompts import build_mode_block, build_system_prompt
from backend.tools.base import ToolResult

# ----------------------------------------------------------------------
# Mode prompts
# ----------------------------------------------------------------------


class TestModePrompts:
    """The mode shapes the voice without changing what is true."""

    def test_education_mode_adds_tutor_guidance(self):
        block = build_mode_block("education")
        assert "tutoring" in block.lower()
        assert "study_generator" in block, "must point the model at the tool"
        assert "scaffold" in block.lower()

    def test_business_mode_demands_concrete_kenyan_advice(self):
        block = build_mode_block("business")
        assert "shilling" in block.lower() or "KSh" in block
        assert "M-Pesa" in block
        assert "business_advisor" in block

    def test_internet_mode_adds_nothing(self):
        """Internet is the default voice, so it must not add a block."""
        assert build_mode_block("internet") == ""

    def test_unknown_mode_is_treated_as_internet(self):
        assert build_mode_block("cooking") == ""
        assert build_mode_block(None) == ""

    def test_mode_is_case_insensitive(self):
        assert build_mode_block("EDUCATION") == build_mode_block("education")

    def test_system_prompt_includes_the_mode(self):
        prompt = build_system_prompt(mode="education")
        assert "EDUCATION MODE" in prompt

    def test_system_prompt_includes_artifact_context(self):
        prompt = build_system_prompt(artifacts=["study material on 'Anatomy' (secondary)"])
        assert "MATERIAL YOU GENERATED EARLIER" in prompt
        assert "Anatomy" in prompt

    def test_artifact_block_tells_the_model_to_extend(self):
        prompt = build_system_prompt(artifacts=["a study set"])
        assert "make it harder" in prompt.lower()

    def test_no_artifacts_means_no_block(self):
        assert "MATERIAL YOU GENERATED" not in build_system_prompt(mode="education")


# ----------------------------------------------------------------------
# Artifact persistence
# ----------------------------------------------------------------------


class FakeItem:
    """Stands in for a MemoryItem row, timestamped like the real table."""

    _counter = 0

    def __init__(self, content, extra_metadata=None):
        from datetime import datetime, timedelta, timezone

        FakeItem._counter += 1
        self.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=FakeItem._counter
        )
        self.content = content
        self.extra_metadata = extra_metadata or {}


class FakeMemory:
    """Records what would have been written, and refuses on demand."""

    def __init__(self, reject=False):
        self.written: list[dict] = []
        self.reject = reject
        self.items: list[FakeItem] = []

    def remember(self, **kwargs):
        if self.reject:
            raise ValueError("refused by policy")
        self.written.append(kwargs)
        item = FakeItem(
            kwargs["content"], kwargs.get("extra_metadata") or {}
        )
        self.items.append(item)
        return item

    def recall(self, query, user_id=None, limit=5):
        return self.items[:limit]

    def list_all(self, user_id=None, category=None):
        return list(self.items)


STUDY = {
    "topic": "Human Anatomy",
    "level": "secondary",
    "practiceQuestions": [
        {"question": "Q1", "answer": "A1", "explanation": "E1"},
        {"question": "Q2", "answer": "A2", "explanation": "E2"},
    ],
    "notes": ["note"],
}

BUSINESS = {
    "business": "Beans & Cereals",
    "goal": "More customers",
    "marketing": ["Join a WhatsApp group"],
    "sevenDayPlan": [{"day": "Day 1", "action": "Visit suppliers"}],
}


def ok(tool, data):
    return ToolResult(tool=tool, ok=True, data=data)


class TestArtifactStore:
    """Generated material is kept; everything else is refused."""

    def test_only_known_tools_produce_artifacts(self):
        assert artifact_kind("study_generator") == "study"
        assert artifact_kind("business_advisor") == "business"
        assert artifact_kind("web_search") is None
        assert artifact_kind("calculator") is None

    def test_study_material_is_persisted(self):
        memory = FakeMemory()
        store = ArtifactStore(memory)
        meta = store.record_from_result(ok("study_generator", STUDY), user_id="u1")
        assert meta is not None
        assert meta["artifact"] == "study"
        assert meta["subject"] == "Human Anatomy"
        assert meta["questionCount"] == 2
        assert memory.written[0]["category"] == "project"

    def test_business_plan_is_persisted(self):
        memory = FakeMemory()
        store = ArtifactStore(memory)
        meta = store.record_from_result(ok("business_advisor", BUSINESS))
        assert meta["artifact"] == "business"
        assert meta["subject"] == "Beans & Cereals"

    def test_stored_content_round_trips(self):
        memory = FakeMemory()
        store = ArtifactStore(memory)
        store.record_from_result(ok("study_generator", STUDY))
        kind, data = store.load_artifact("Human Anatomy")
        assert kind == "study"
        assert data["practiceQuestions"][0]["question"] == "Q1"

    def test_a_failed_tool_is_never_stored(self):
        memory = FakeMemory()
        store = ArtifactStore(memory)
        failed = ToolResult(tool="study_generator", ok=False, error="nope")
        assert store.record_from_result(failed) is None
        assert memory.written == []

    def test_an_unrelated_tool_is_never_stored(self):
        memory = FakeMemory()
        store = ArtifactStore(memory)
        assert store.record_from_result(ok("web_search", {"sources": []})) is None
        assert memory.written == []

    def test_material_without_a_subject_is_not_stored(self):
        """A half-built generation must not be persisted."""
        memory = FakeMemory()
        store = ArtifactStore(memory)
        assert store.record_from_result(ok("study_generator", {"notes": ["x"]})) is None
        assert store.record_from_result(ok("business_advisor", {})) is None
        assert memory.written == []

    def test_a_rejected_write_never_breaks_the_turn(self):
        """Policy refusal must be swallowed: the answer is still delivered."""
        memory = FakeMemory(reject=True)
        store = ArtifactStore(memory)
        assert store.record_from_result(ok("study_generator", STUDY)) is None

    def test_oversized_material_is_truncated(self):
        memory = FakeMemory()
        store = ArtifactStore(memory)
        huge = dict(STUDY, notes=["x" * 50000])
        store.record_from_result(ok("study_generator", huge))
        assert len(memory.written[0]["content"]) <= 6000

    def test_recall_returns_prompt_lines(self):
        memory = FakeMemory()
        store = ArtifactStore(memory)
        store.record_from_result(ok("study_generator", STUDY))
        lines = store.recall_lines("more anatomy questions")
        assert len(lines) == 1
        assert "Human Anatomy" in lines[0]
        assert "2 practice question" in lines[0]

    def test_recall_of_nothing_is_empty(self):
        assert ArtifactStore(FakeMemory()).recall_lines("anything") == []

    def test_recall_ignores_a_blank_query(self):
        memory = FakeMemory()
        ArtifactStore(memory).record_from_result(ok("study_generator", STUDY))
        assert ArtifactStore(memory).recall_lines("   ") == []

    def test_recall_ignores_non_artifact_memories(self):
        """An ordinary memory must not be presented as generated material."""
        memory = FakeMemory()
        memory.items.append(
            FakeItem("The user prefers Kiswahili", {"category": "preference"})
        )
        assert ArtifactStore(memory).recall_lines("preference") == []

    def test_load_by_partial_subject(self):
        memory = FakeMemory()
        store = ArtifactStore(memory)
        store.record_from_result(ok("study_generator", STUDY))
        kind, data = store.load_artifact("anatomy")
        assert kind == "study"
        assert data["level"] == "secondary"

    def test_load_unknown_subject_is_empty(self):
        memory = FakeMemory()
        ArtifactStore(memory).record_from_result(ok("study_generator", STUDY))
        assert ArtifactStore(memory).load_artifact("Photosynthesis") == (None, None)

    def test_stored_metadata_is_json_serialisable(self):
        memory = FakeMemory()
        ArtifactStore(memory).record_from_result(ok("business_advisor", BUSINESS))
        json.dumps(memory.written[0]["extra_metadata"])


class TestLatestForMode:
    """A bare follow-up must still find what was just generated."""

    def _seed(self, memory, kind, subject):
        payload = (
            {"topic": subject, "level": "secondary", "practiceQuestions": []}
            if kind == "study"
            else {"business": subject, "goal": "grow"}
        )
        tool = "study_generator" if kind == "study" else "business_advisor"
        ArtifactStore(memory).record_from_result(ok(tool, payload))

    def test_finds_the_newest_study_material(self):
        memory = FakeMemory()
        self._seed(memory, "study", "Human Anatomy")
        lines = ArtifactStore(memory).latest_for_mode("study")
        assert len(lines) == 1
        assert "Human Anatomy" in lines[0]

    def test_kind_is_respected(self):
        memory = FakeMemory()
        self._seed(memory, "study", "Anatomy")
        self._seed(memory, "business", "Cereal shop")
        store = ArtifactStore(memory)
        assert "Anatomy" in " ".join(store.latest_for_mode("study"))
        assert "Cereal shop" in " ".join(store.latest_for_mode("business"))

    def test_unknown_kind_is_refused(self):
        assert ArtifactStore(FakeMemory()).latest_for_mode("nonsense") == []

    def test_empty_when_nothing_stored(self):
        assert ArtifactStore(FakeMemory()).latest_for_mode("study") == []


class TestOrchestratorRecallWiring:
    """The orchestrator must reach the store with correct arguments.

    Regression: `recall_lines` takes `user_id` keyword-only, but the
    orchestrator passed it positionally through `asyncio.to_thread`. The
    resulting TypeError was swallowed by the guard, so artifact recall
    silently never happened and follow-ups lost their material.
    """

    def test_recall_lines_rejects_positional_user_id(self):
        """Guard the signature so the mistake cannot come back."""
        import inspect

        signature = inspect.signature(ArtifactStore.recall_lines)
        parameter = signature.parameters["user_id"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_latest_for_mode_rejects_positional_user_id(self):
        import inspect

        parameter = inspect.signature(ArtifactStore.latest_for_mode).parameters["user_id"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_keyword_call_returns_artifacts(self):
        memory = FakeMemory()
        ArtifactStore(memory).record_from_result(ok("study_generator", STUDY))
        assert ArtifactStore(memory).recall_lines("anatomy", user_id="u1")

    def test_orchestrator_passes_user_id_as_keyword(self):
        """Inspect the source rather than trusting a passing integration test."""
        import inspect

        from backend.agent.orchestrator import SautiOrchestrator

        source = inspect.getsource(SautiOrchestrator.handle)
        assert "recall_lines, message, user_id=user_id" in source
        assert "recall_lines, message, user_id\n" not in source


class TestFollowUpSeesNewestFirst:
    """A follow-up means the material generated moments ago, not any match.

    "Make them harder" lexically matches older artifacts that merely mention
    questions, so a pure relevance search can hand back stale material.
    """

    def test_newest_is_ordered_before_relevance_hits(self):
        memory = FakeMemory()
        store = ArtifactStore(memory)
        store.record_from_result(ok("study_generator", dict(STUDY, topic="Old Topic")))
        store.record_from_result(ok("study_generator", dict(STUDY, topic="New Topic")))

        newest = store.latest_for_mode("study", limit=1)
        assert "New Topic" in newest[0]

        related = store.recall_lines("more questions")
        combined = newest + [line for line in related if line not in newest]
        assert "New Topic" in combined[0], "the freshest material must lead"
        assert len(combined) == len(set(combined)), "no duplicates"
