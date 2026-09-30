"""End-to-end tests for the SAUTI orchestrator and HTTP API."""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.agent.orchestrator import SautiOrchestrator
from backend.config.settings import Settings
from backend.memory.repository import MemoryRepository
from backend.memory.service import MemoryService
from backend.tools.registry import ToolRegistry
from backend.tools.calculator import CalculatorTool
from backend.tools.web_reader import WebReaderTool
from backend.tools.web_search import WebSearchTool


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _stub_provider(monkeypatch):
    """Pin the search provider so these tests never depend on ambient env.

    Individual tests override it to exercise failure paths.
    """
    monkeypatch.setenv("SEARCH_PROVIDER", "stub")
    monkeypatch.setenv("LLM_API_KEY", "")
    from backend.config.settings import get_settings

    get_settings(refresh=True)
    yield
    get_settings(refresh=True)


def make_agent(provider="stub", **kwargs):
    settings = Settings(search_provider=provider, **kwargs)
    registry = ToolRegistry(settings)
    registry.register_all([
        WebSearchTool(settings),
        WebReaderTool(settings),
        CalculatorTool(settings),
    ])
    memory = MemoryService(settings, MemoryRepository())
    return SautiOrchestrator(settings=settings, registry=registry, memory=memory)


@pytest.fixture
def agent(app):
    with app.app_context():
        yield make_agent()


class TestOrchestratorLanguages:
    def test_english_current_question_uses_marketplace(self, agent):
        turn = run(agent.handle("What is the current price of maize in Kenya?"))
        assert turn.language == "en"
        assert turn.reply_language == "en"
        assert "search_marketplace" in turn.used_tools
        assert turn.marketplace is not None

    def test_swahili_current_question(self, agent):
        turn = run(agent.handle("Bei ya mahindi Kenya kwa sasa ni kiasi gani?"))
        assert turn.language == "sw"
        assert turn.reply_language == "sw"
        assert "search_marketplace" in turn.used_tools

    def test_french_current_question(self, agent):
        turn = run(agent.handle("Quel est le prix actuel du maïs au Kenya ?"))
        assert turn.language == "fr"
        assert turn.reply_language == "fr"
        assert "search_marketplace" in turn.used_tools

    def test_mixed_language_question(self, agent):
        turn = run(agent.handle("Sauti tafuta current price ya maize Kenya."))
        assert turn.language == "sw"
        assert turn.is_mixed is True
        assert "search_marketplace" in turn.used_tools

    def test_vehicle_question_uses_marketplace(self, agent):
        turn = run(agent.handle("How much is a Mercedes GLE in Kenya right now?"))
        assert "search_marketplace" in turn.used_tools
        assert turn.marketplace is not None

    def test_stable_question_skips_research(self, agent):
        turn = run(agent.handle("What is Python?"))
        assert turn.used_tools == []
        assert turn.research_performed is False

    def test_explicit_language_request_is_honoured(self, agent):
        turn = run(agent.handle("What is the current maize price? Answer in Kiswahili."))
        assert turn.language == "en"
        assert turn.reply_language == "sw"

    def test_tools_can_be_disabled_by_caller(self, agent):
        turn = run(agent.handle("What is the current maize price in Kenya?", use_tools=False))
        assert turn.used_tools == []


class TestOrchestratorHonesty:
    def test_failed_search_is_reported_not_invented(self, agent):
        """With a broken provider SAUTI must say so, not guess a price."""
        broken = make_agent(provider="failing")
        turn = run(broken.handle("What is the USD KES exchange rate today?"))
        assert "web_search" in turn.used_tools
        assert turn.research_available is False
        assert turn.sources == []
        lowered = turn.reply.lower()
        assert "unavailable" in lowered or "indisponible" in lowered
        # Critically: no invented currency figure.
        assert "ksh" not in lowered

    def test_failure_message_is_localised(self, agent):
        broken = make_agent(provider="failing")
        turn = run(broken.handle("Quel est le taux de change USD KES aujourd'hui ?"))
        assert "indisponible" in turn.reply.lower()

    def test_offline_fallback_is_flagged(self, agent):
        turn = run(agent.handle("What is the USD KES exchange rate today?"))
        assert turn.offline_fallback is True
        assert "no language model is configured" in turn.reply

    def test_answer_contains_no_phantom_figures(self, agent):
        """Figures in the answer must come from real retrieved evidence."""
        turn = run(agent.handle("What is the current price of maize in Kenya?"))
        cited = " ".join(s.snippet for s in turn.sources)
        for figure in ("3,400", "3,250"):
            if figure in turn.reply:
                assert figure in cited

    def test_reports_source_dates_honestly(self, agent):
        turn = run(agent.handle("What is the current price of maize in Kenya?"))
        for source in turn.sources:
            assert source.published_at is None or len(source.published_at) >= 10


class TestOrchestratorActivity:
    def test_activity_lists_research_steps(self, agent):
        turn = run(agent.handle("What is the USD KES exchange rate today?"))
        assert any("Searching" in step for step in turn.activity)

    def test_no_activity_for_stable_question(self, agent):
        turn = run(agent.handle("What is Python?"))
        assert turn.activity == []

    def test_never_exposes_reasoning(self, agent):
        turn = run(agent.handle("What is the current price of maize in Kenya?"))
        joined = " ".join(turn.activity).lower()
        for leak in ("chain of thought", "system prompt", "reasoning:", "my instructions"):
            assert leak not in joined


class TestOrchestratorMemory:
    def test_recalls_stored_preference(self, agent):
        from flask import current_app

        with current_app.app_context():
            agent.memory.remember_preference("language", "Kiswahili")
        turn = run(agent.handle("What is the current maize price in Kenya?"))
        assert any("Kiswahili" in item for item in turn.memory_used)

    def test_no_memories_is_not_an_error(self, agent):
        turn = run(agent.handle("What is the current maize price?"))
        assert turn.memory_used == []


class TestChatApi:
    def test_chat_returns_agent_fields(self, client, monkeypatch):
        monkeypatch.setenv("SEARCH_PROVIDER", "stub")
        response = client.post("/api/chat", json={"message": "What is the current price of maize?"})
        assert response.status_code == 200
        data = response.get_json()
        for field in ("message", "language", "used_tools", "sources", "activity",
                      "research_performed", "conversation_id", "request_id"):
            assert field in data
        assert data["used_tools"] == ["search_marketplace"]
        assert data["conversation_id"]

    def test_chat_keeps_legacy_fields(self, client):
        """Existing SautiPay clients must keep working."""
        response = client.post("/api/chat", json={"message": "Hello"})
        data = response.get_json()
        for field in ("response", "intent", "confidence"):
            assert field in data

    def test_chat_swahili(self, client):
        data = client.post(
            "/api/chat", json={"message": "Bei ya mahindi Kenya kwa sasa ni kiasi gani?"}
        ).get_json()
        assert data["language"] == "sw"

    def test_chat_french(self, client):
        data = client.post(
            "/api/chat", json={"message": "Quel est le prix actuel du maïs au Kenya ?"}
        ).get_json()
        assert data["language"] == "fr"

    def test_chat_mixed(self, client):
        data = client.post(
            "/api/chat", json={"message": "Sauti tafuta current price ya maize Kenya."}
        ).get_json()
        assert data["language"] == "sw"
        assert data["is_mixed"] is True

    def test_chat_stable_no_tools(self, client):
        data = client.post("/api/chat", json={"message": "What is Python?"}).get_json()
        assert data["used_tools"] == []

    def test_conversation_history_persists(self, client):
        first = client.post("/api/chat", json={"message": "Hello"}).get_json()
        conversation_id = first["conversation_id"]
        client.post(
            "/api/chat",
            json={"message": "And what is photosynthesis?", "conversation_id": conversation_id},
        )
        history = client.get(f"/api/chat/history/{conversation_id}").get_json()
        assert len(history["messages"]) == 4

    def test_history_missing_conversation(self, client):
        assert client.get("/api/chat/history/nope").status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {"message": ""},
            {"message": "   "},
            {"message": "a" * 5001},
            {"message": "hi", "language": "zz"},
        ],
    )
    def test_chat_validation(self, client, body):
        assert client.post("/api/chat", json=body).status_code == 400

    def test_chat_requires_json(self, client):
        assert client.post("/api/chat", data="plain", content_type="text/plain").status_code == 400

    def test_chat_never_returns_secrets(self, client):
        data = client.post("/api/chat", json={"message": "What is the maize price?"}).get_json()
        blob = json.dumps(data).lower()
        for secret in ("llm_api_key", "search_api_key", "system prompt", "authorization"):
            assert secret not in blob


class TestResearchApi:
    def test_research_returns_structured_data(self, client):
        response = client.post("/api/research", json={"query": "current maize price Kenya"})
        assert response.status_code == 200
        data = response.get_json()
        assert data["ok"] is True
        assert data["sources"]

    def test_research_failure_is_503(self, client, monkeypatch):
        monkeypatch.setenv("SEARCH_PROVIDER", "failing")
        response = client.post("/api/research", json={"query": "current maize price Kenya"})
        assert response.status_code == 503

    def test_research_reports_failure_with_503(self, client, monkeypatch):
        monkeypatch.setenv("SEARCH_PROVIDER", "failing")
        response = client.post("/api/research", json={"query": "current maize price Kenya"})
        assert response.status_code == 503
        data = response.get_json()
        assert data["ok"] is False
        assert data["error"]
        assert data["sources"] == []

    def test_research_validation(self, client):
        assert client.post("/api/research", json={"query": ""}).status_code == 400
        assert client.post("/api/research", json={}).status_code == 400

    def test_research_status_reports_availability(self, client):
        data = client.get("/api/research/status").get_json()
        assert "available" in data
        assert "llm_configured" in data


class TestMemoryApi:
    def test_create_and_list(self, client):
        created = client.post(
            "/api/memory", json={"content": "Prefers Kiswahili", "category": "preference"}
        )
        assert created.status_code == 201
        listed = client.get("/api/memory").get_json()
        assert listed["total"] >= 1

    def test_sensitive_content_rejected(self, client):
        response = client.post("/api/memory", json={"content": "my password is hunter2"})
        assert response.status_code == 400
        assert response.get_json()["rejected"] is True

    def test_recall_endpoint(self, client):
        client.post("/api/memory", json={"content": "Loves Kiswahili replies", "category": "preference"})
        data = client.post("/api/memory/recall", json={"query": "language preference"}).get_json()
        assert data["total"] >= 1

    def test_recall_requires_query(self, client):
        assert client.post("/api/memory/recall", json={}).status_code == 400

    def test_delete_by_key(self, client):
        client.post("/api/memory", json={"content": "temp note", "key": "temp"})
        response = client.delete("/api/memory", json={"key": "temp"})
        assert response.status_code == 200
        assert response.get_json()["deleted"] >= 1

    def test_delete_requires_identifier(self, client):
        assert client.delete("/api/memory", json={}).status_code == 400

    def test_validation(self, client):
        assert client.post("/api/memory", json={"content": ""}).status_code == 400
        assert client.post("/api/memory", json={"content": "x", "importance": 5}).status_code == 400


class TestToolsApi:
    def test_lists_tools(self, client):
        data = client.get("/api/tools").get_json()
        names = {t["name"] for t in data["tools"]}
        # Platform data tools must be present alongside the external ones.
        assert {"search_marketplace", "get_vendor_profile", "get_product_details",
                "search_news"} <= names
        assert {"calculator", "web_reader", "web_search"} <= names

    def test_executes_tool(self, client):
        data = client.post(
            "/api/tools/execute", json={"name": "calculator", "arguments": {"expression": "6*7"}}
        ).get_json()
        assert data["ok"] is True
        assert data["data"]["result"] == 42

    def test_rejects_code_injection(self, client):
        response = client.post(
            "/api/tools/execute",
            json={"name": "calculator", "arguments": {"expression": "__import__('os')"}},
        )
        assert response.status_code == 400
        assert response.get_json()["ok"] is False

    def test_unknown_tool(self, client):
        response = client.post("/api/tools/execute", json={"name": "nope", "arguments": {}})
        assert response.status_code == 400

    def test_validation(self, client):
        assert client.post("/api/tools/execute", json={}).status_code == 400
        assert client.post("/api/tools/execute", data="x", content_type="text/plain").status_code == 400


class TestDataSafety:
    def test_sauti_workflow_preserves_existing_data(self, client, app):
        from backend.db import db
        from backend.models import Conversation, Message, User

        with app.app_context():
            user = User(phone_number="+254700999000", name="Legacy")
            db.session.add(user)
            db.session.commit()
            conversation = Conversation(user_id=user.id)
            db.session.add(conversation)
            db.session.commit()
            db.session.add(Message(conversation_id=conversation.id, role="user", content="hi"))
            db.session.commit()
            user_id, conversation_id = user.id, conversation.id

        client.post("/api/chat", json={"message": "What is the current maize price?"})
        client.post("/api/memory", json={"content": "a stored preference"})

        with app.app_context():
            assert db.session.get(User, user_id) is not None
            restored = db.session.get(Conversation, conversation_id)
            assert restored is not None
            assert len(restored.messages) == 1


class TestStoredLanguagePreference:
    """A stored language preference must shape replies."""

    def test_preference_applied_to_new_message(self, agent):
        from flask import current_app

        with current_app.app_context():
            agent.memory.remember_preference("language", "Kiswahili")

        turn = run(agent.handle("What is the current price of maize in Kenya?"))
        assert turn.reply_language == "sw"

    def test_explicit_request_overrides_preference(self, agent):
        from flask import current_app

        with current_app.app_context():
            agent.memory.remember_preference("language", "Kiswahili")

        turn = run(
            agent.handle("What is the current maize price? Answer in French.")
        )
        assert turn.reply_language == "fr"

    def test_caller_pinned_language_wins(self, agent):
        from flask import current_app

        with current_app.app_context():
            agent.memory.remember_preference("language", "Kiswahili")

        turn = run(
            agent.handle("What is the current maize price?", language_hint="en")
        )
        assert turn.reply_language == "en"

    def test_language_name_mapping(self):
        from backend.agent.language import language_from_name

        assert language_from_name("Kiswahili") == "sw"
        assert language_from_name("English") == "en"
        assert language_from_name("Réponds en français") == "fr"
        assert language_from_name("") is None


class TestSourceDeduplication:
    """A tool reporting the same URL twice must yield one citation."""

    def test_no_duplicate_urls_in_citations(self, agent):
        turn = run(agent.handle("What is the USD KES exchange rate today?"))
        urls = [s.url for s in turn.sources]
        assert len(urls) == len(set(urls)), f"duplicate sources: {urls}"

    def test_no_duplicate_urls_in_api_response(self, client):
        data = client.post(
            "/api/chat", json={"message": "What is the USD KES exchange rate today?"}
        ).get_json()
        urls = [s["url"] for s in data["sources"]]
        assert len(urls) == len(set(urls))


class TestEngineOutcomeSignalling:
    """A failed provider must be visible to the client.

    ``engine`` names the *configured* provider, so it reads "groq" whether or
    not groq answered. These tests pin the fields a client must use instead,
    so a provider outage can never be reported as a successful turn again.
    """

    def test_offline_turn_is_not_reported_as_engine_ok(self, agent):
        """No provider configured means no engine answered."""
        turn = run(agent.handle("What is the USD KES exchange rate today?"))
        assert turn.engine_ok is False
        assert turn.offline_fallback is True

    def test_degraded_is_true_when_engine_did_not_answer(self, agent):
        turn = run(agent.handle("What is the USD KES exchange rate today?"))
        assert turn.to_dict()["degraded"] is True

    def test_payload_exposes_outcome_fields(self, client):
        data = client.post("/api/chat", json={"message": "Hello"}).get_json()
        for field in ("engine", "engine_ok", "engine_error", "degraded"):
            assert field in data, f"missing {field}"

    def test_groq_failure_is_reported_not_masked(self, app, monkeypatch):
        """A groq turn that comes back not-ok must surface as a failure."""
        import httpx

        from backend.integrations.groq import client as groq_client_mod

        async def handler(request):
            return httpx.Response(
                429,
                json={
                    "error": {
                        "message": "Rate limit reached",
                        "type": "rate_limit_error",
                    }
                },
            )

        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        # Retry policy is a module constant read from the environment at import
        # time, so it has to be patched directly to keep the test quick.
        monkeypatch.setattr(groq_client_mod, "MAX_ATTEMPTS", 1)
        monkeypatch.setattr(groq_client_mod, "BACKOFF_BASE", 0.0)

        settings = Settings(
            groq_api_key="gsk_TEST-not-a-real-key-000000000000",
            groq_model="qwen/qwen3.8-27b",
            search_provider="stub",
        )
        orchestrator = SautiOrchestrator(settings=settings)

        with app.app_context():
            turn = run(orchestrator.handle("What is the capital of Kenya?"))

        assert turn.engine_ok is False
        assert turn.engine_error, "a failed engine must explain itself"
        assert turn.to_dict()["degraded"] is True
        # The failure reason must never leak provider internals.
        assert "qwen" not in turn.engine_error
        assert "gsk_" not in turn.engine_error
