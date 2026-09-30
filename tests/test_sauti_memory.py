"""Tests for SAUTI memory: persistence, retrieval, deletion and safety."""
from __future__ import annotations

import pytest

from backend.config.settings import Settings
from backend.memory.models import MemoryItem
from backend.memory.repository import MemoryRepository
from backend.memory.service import MemoryRejected, MemoryService
from backend.models import Conversation, Message, User


@pytest.fixture
def memory(app):
    return MemoryService(Settings(memory_enabled=True), MemoryRepository())


class TestMemoryCreation:
    def test_creates_item(self, memory, app):
        with app.app_context():
            item = memory.remember("I am working on a maize cooperative", category="project")
            assert item.id
            assert item.category == "project"
            assert item.is_active is True

    def test_rejects_empty_content(self, memory, app):
        with app.app_context():
            with pytest.raises(ValueError):
                memory.remember("   ")

    def test_rejects_unknown_category(self, memory, app):
        with app.app_context():
            with pytest.raises(ValueError):
                memory.remember("something", category="not_a_category")

    def test_links_to_user_and_conversation(self, memory, app):
        with app.app_context():
            user = User(phone_number="+254700555444", name="Memory User")
            from backend.db import db

            db.session.add(user)
            db.session.commit()
            conversation = Conversation(user_id=user.id)
            db.session.add(conversation)
            db.session.commit()

            item = memory.remember(
                "Prefers morning briefings",
                category="preference",
                user_id=user.id,
                conversation_id=conversation.id,
            )
            assert item.user_id == user.id
            assert item.conversation_id == conversation.id

    def test_upsert_preference_replaces(self, memory, app):
        with app.app_context():
            first = memory.remember_preference("language", "Kiswahili")
            second = memory.remember_preference("language", "English")
            assert first.id == second.id
            assert second.content == "English"
            assert memory.get_preference("language") == "English"


class TestMemorySafety:
    @pytest.mark.parametrize(
        "content",
        [
            "My API key is sk-abc123",
            "the password is hunter2",
            "my bank account number is 12345678901",
            "card number 4111111111111111",
            "my private key is stored here",
        ],
    )
    def test_refuses_sensitive_content(self, memory, app, content):
        with app.app_context():
            with pytest.raises(MemoryRejected):
                memory.remember(content)

    def test_refuses_forbidden_category(self, memory, app):
        with app.app_context():
            with pytest.raises(MemoryRejected):
                memory.remember("my pin is 1234", category="credential")

    def test_allows_ordinary_content(self, memory, app):
        with app.app_context():
            assert memory.remember("I prefer concise answers") is not None

    def test_disabled_memory_refuses_writes(self, app):
        with app.app_context():
            service = MemoryService(Settings(memory_enabled=False), MemoryRepository())
            with pytest.raises(MemoryRejected):
                service.remember("anything")


class TestMemoryRetrieval:
    def test_recall_matches_relevant_query(self, memory, app):
        with app.app_context():
            memory.remember("My preferred language is Kiswahili", category="preference", importance=0.9)
            memory.remember("I am building a solar installer in Kisumu", category="project")
            found = memory.recall("what language do I prefer")
            assert any("Kiswahili" in item.content for item in found)

    def test_recall_returns_empty_for_unrelated(self, memory, app):
        with app.app_context():
            memory.remember("Kiswahili language preference")
            assert memory.recall("zeppelin navigation") == []

    def test_recall_empty_query(self, memory, app):
        with app.app_context():
            memory.remember("anything")
            assert memory.recall("") == []

    def test_recall_prompt_lines(self, memory, app):
        with app.app_context():
            memory.remember("Prefers metric units", category="preference")
            lines = memory.recall_as_prompt_lines("units")
            assert lines and lines[0].startswith("[preference]")

    def test_recall_increments_access_count(self, memory, app):
        with app.app_context():
            item = memory.remember("Prefers metric units", importance=0.9)
            memory.recall("units")
            assert MemoryRepository().get(item.id).access_count >= 1

    def test_list_all_filtered_by_user(self, memory, app):
        from backend.db import db

        with app.app_context():
            user = User(phone_number="+254700333222")
            db.session.add(user)
            db.session.commit()
            memory.remember("user one note", user_id=user.id)
            memory.remember("global note")

            mine = memory.list_all(user_id=user.id)
            assert len(mine) == 1
            assert mine[0].content == "user one note"


class TestMemoryDeletion:
    def test_delete_by_id(self, memory, app):
        with app.app_context():
            item = memory.remember("temporary note")
            assert memory.forget(item.id) is True
            assert memory.repository.get(item.id) is None

    def test_delete_missing_returns_false(self, memory, app):
        with app.app_context():
            assert memory.forget("does-not-exist") is False

    def test_forget_by_key_deactivates(self, memory, app):
        with app.app_context():
            memory.remember("language Kiswahili", key="language")
            removed = memory.forget_matching("language")
            assert removed == 1
            assert memory.list_all() == []

    def test_deleted_memory_not_recalled(self, memory, app):
        with app.app_context():
            memory.remember("Prefers Kiswahili always", importance=0.9)
            assert memory.recall("Kiswahili")
            memory.forget_matching("Kiswahili")
            assert memory.recall("Kiswahili") == []


class TestPreferenceDetection:
    def test_detects_english_preference(self, memory, app):
        with app.app_context():
            found = memory.detect_preference_statement(
                "My preferred language is Kiswahili."
            )
            assert found == {"key": "language", "value": "Kiswahili"}

    def test_detects_swahili_preference(self, memory, app):
        with app.app_context():
            found = memory.detect_preference_statement("Na upendelea majibu mafupi")
            assert found is not None

    def test_returns_none_for_ordinary_text(self, memory, app):
        with app.app_context():
            assert memory.detect_preference_statement("What is the maize price?") is None

    def test_detection_does_not_store_anything(self, memory, app):
        with app.app_context():
            memory.detect_preference_statement("My preferred language is French.")
            assert memory.list_all() == []


class TestMemoryPersistence:
    def test_memory_survives_a_new_service_instance(self, app):
        """Simulates a process restart: new service, same database."""
        with app.app_context():
            first = MemoryService(Settings(), MemoryRepository())
            item = first.remember(
                "Durable preference: reply in Kiswahili", category="preference", importance=0.9
            )
            memory_id = item.id

        # A completely fresh service + repository, as after a restart.
        with app.app_context():
            second = MemoryService(Settings(), MemoryRepository())
            restored = second.repository.get(memory_id)
            assert restored is not None
            assert "Kiswahili" in restored.content
            assert second.recall("Kiswahili")

    def test_existing_conversations_survive_memory_use(self, memory, app):
        from backend.db import db

        with app.app_context():
            user = User(phone_number="+254700111222", name="Existing")
            db.session.add(user)
            db.session.commit()
            conversation = Conversation(user_id=user.id)
            db.session.add(conversation)
            db.session.commit()
            message = Message(conversation_id=conversation.id, role="user", content="hello")
            db.session.add(message)
            db.session.commit()
            conversation_id = conversation.id

            memory.remember("something worth keeping", user_id=user.id)

            assert db.session.get(Conversation, conversation_id) is not None
            assert len(db.session.get(Conversation, conversation_id).messages) == 1
