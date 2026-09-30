"""Tests for Phase 2: intent routing, place tools, typed cards.

Covers the routing contract, the mode bias, the no-fabrication guarantee of the
place tools, and the shape of every card type. Nothing here touches the
network: Nominatim is exercised through a mocked transport.
"""
from __future__ import annotations

import json

import httpx
import pytest

from backend.agent.cards import cards_from_results
from backend.agent.intent_router import (
    FALLBACK_TOOLS,
    MODE_AFFINITY,
    MODE_BIAS,
    Intent,
    IntentRouter,
    allowed_tools,
    normalise_mode,
    score_by_rules,
)
from backend.config.settings import Settings
from backend.schemas.sauti import SautiChatRequest
from backend.tools.base import ToolResult
from backend.tools.place_search import PlaceSearchTool, _normalise_place
from backend.tools.source_links import SourceLinksTool, build_links

FAKE_KEY = "gsk_TEST-not-a-real-key-000000000000"


def run(coro):
    """Run a coroutine to completion."""
    import asyncio

    return asyncio.run(coro)


def settings(**overrides) -> Settings:
    return Settings(groq_api_key=FAKE_KEY, **overrides)


@pytest.fixture
def groq_http(monkeypatch):
    """Intercept the AsyncClient built by Groq-backed tools."""
    holder: dict = {"handler": None}
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        if holder["handler"] is not None:
            kwargs["transport"] = httpx.MockTransport(holder["handler"])
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return lambda handler: holder.__setitem__("handler", handler)


# ----------------------------------------------------------------------
# Routing rules
# ----------------------------------------------------------------------


class TestRoutingRules:
    """The cheap first stage of routing."""

    @pytest.mark.parametrize(
        "message,intent",
        [
            ("Find University of Nairobi", Intent.PLACE_SEARCH),
            ("find me a hospital near me", Intent.PLACE_SEARCH),
            ("find restaurants near Westlands", Intent.PLACE_SEARCH),
            ("Natafuta beans Yanza", Intent.PRODUCT_SEARCH),
            ("Find me a blue Mercedes G-Wagon with 600 horsepower", Intent.PRODUCT_SEARCH),
            ("give me anatomy revision questions", Intent.STUDY_MATERIAL),
            ("I sell beans in Nairobi, how can I market my cereal shop?", Intent.MARKETING),
            ("help me start a cereal business", Intent.BUSINESS_ADVICE),
            ("what is the price of maize", Intent.PRICE_QUERY),
            ("how do I get to Westlands", Intent.DIRECTIONS),
            ("help me understand this python code", Intent.CODING),
            ("thermodynamics practice for mechanical engineering", Intent.ENGINEERING),
            ("hello there", Intent.GENERAL_CHAT),
        ],
    )
    def test_known_phrases_route_correctly(self, message, intent):
        scores = score_by_rules(message)
        assert scores, f"no rule matched: {message}"
        best = max(scores.items(), key=lambda kv: kv[1])
        assert best[0] is intent, f"{message} -> {best[0].value} (want {intent.value})"

    def test_every_intent_is_reachable_by_some_keyword(self):
        """No intent may be dead weight with no keyword evidence.

        GENERAL_INFORMATION is the one deliberate exception: it is the
        router's "nothing specific matched" bucket, so it has no keywords.
        """
        unreachable = [
            intent
            for intent in Intent
            if intent is not Intent.GENERAL_INFORMATION
            and not score_by_rules(INTENT_SAMPLES[intent])
        ]
        assert not unreachable, f"unreachable intents: {[i.value for i in unreachable]}"

    def test_specific_phrases_outrank_single_words(self):
        """'market my' must beat the stray word 'shop'."""
        scores = score_by_rules("how do I market my shop")
        assert scores[Intent.MARKETING] > scores.get(Intent.PRODUCT_SEARCH, 0)

    def test_a_place_request_is_not_treated_as_shopping(self):
        scores = score_by_rules("find a hospital near me")
        assert scores[Intent.PLACE_SEARCH] > scores.get(Intent.PRODUCT_SEARCH, 0)

    def test_ambiguous_find_me_does_not_imply_a_place(self):
        """'find me a blue Mercedes' is shopping, not a venue."""
        scores = score_by_rules("find me a blue Mercedes")
        assert Intent.PLACE_SEARCH not in scores

    def test_empty_message_scores_nothing(self):
        assert score_by_rules("") == {}
        assert score_by_rules("   ") == {}

    def test_swahili_and_french_are_covered(self):
        assert Intent.PRODUCT_SEARCH in score_by_rules("Natafuta wauzaji wa mahindi")
        assert Intent.PRICE_QUERY in score_by_rules("Quel est le prix du maïs")
        assert Intent.PLACE_SEARCH in score_by_rules("Trouve un hôpital")


#: A representative message for each intent, used to prove none is unreachable.
INTENT_SAMPLES = {
    Intent.GENERAL_INFORMATION: "tell me about yourself",
    Intent.PLACE_SEARCH: "find me a hospital",
    Intent.DIRECTIONS: "how do I get there",
    Intent.LOCATION_SEARCH: "where is the nearest pharmacy",
    Intent.PRODUCT_SEARCH: "I want to buy a phone",
    Intent.VENDOR_SEARCH: "who sells maize in Nairobi",
    Intent.SERVICE_SEARCH: "I need a plumber",
    Intent.PRICE_QUERY: "what is the price of maize",
    Intent.STUDY_MATERIAL: "give me revision questions",
    Intent.EDUCATION: "I am studying biology",
    Intent.CODING: "help with this python code",
    Intent.ENGINEERING: "thermodynamics engineering problem",
    Intent.BUSINESS_ADVICE: "help me start a business",
    Intent.MARKETING: "how do I market my shop",
    Intent.GENERAL_CHAT: "hello there",
}


# ----------------------------------------------------------------------
# Mode bias
# ----------------------------------------------------------------------


class TestModeBias:
    """Mode is a hint, never a lock."""

    def test_modes_are_normalised(self):
        assert normalise_mode("EDUCATION") == "education"
        assert normalise_mode("cooking") == "internet"
        assert normalise_mode(None) == "internet"

    def test_education_mode_favours_learning_intents(self):
        router = IntentRouter(settings())
        result = run(
            router.route("explain thermodynamics", mode="education", request_id="t1")
        )
        assert result.mode == "education"
        assert result.intent in MODE_AFFINITY["education"]

    def test_business_mode_favours_business_intents(self):
        router = IntentRouter(Settings(groq_api_key=""))
        result = run(
            router.route("give me a marketing plan", mode="business", request_id="t2")
        )
        assert result.intent in (Intent.MARKETING, Intent.BUSINESS_ADVICE)

    def test_mode_does_not_lock_a_mismatched_question(self):
        """The spec's requirement: Business mode must still find a hospital."""
        router = IntentRouter(Settings(groq_api_key=""))
        result = run(
            router.route("find me a hospital near me", mode="business", request_id="t3")
        )
        assert result.intent is Intent.PLACE_SEARCH, (
            "mode must bias, not override an obvious intent"
        )

    def test_bias_is_bounded(self):
        assert MODE_BIAS > 0
        assert MODE_BIAS < 10, "an unbounded bias would act as a lock"

    def test_internet_mode_is_unbiased(self):
        assert MODE_AFFINITY["internet"] == ()


# ----------------------------------------------------------------------
# Tool scoping
# ----------------------------------------------------------------------


class TestToolScoping:
    """Only relevant tools are offered, to keep prompts small."""

    def test_small_talk_gets_no_tools(self):
        assert allowed_tools(Intent.GENERAL_CHAT) == ()

    def test_study_gets_the_study_tool(self):
        assert "study_generator" in allowed_tools(Intent.STUDY_MATERIAL)

    def test_place_gets_the_place_tools(self):
        tools = allowed_tools(Intent.PLACE_SEARCH)
        assert "place_search" in tools
        assert "source_links" in tools

    def test_product_gets_marketplace_not_study(self):
        tools = allowed_tools(Intent.PRODUCT_SEARCH)
        assert "search_marketplace" in tools
        assert "study_generator" not in tools

    def test_every_scoped_tool_actually_exists(self):
        from backend.tools.registry import get_registry

        known = set(get_registry(settings()).names())
        for intent, tools in [(i, allowed_tools(i)) for i in Intent]:
            unknown = set(tools) - known
            assert not unknown, f"{intent.value} references unknown tools: {unknown}"

    def test_fallback_is_broad(self):
        assert len(FALLBACK_TOOLS) >= 8


# ----------------------------------------------------------------------
# Place search
# ----------------------------------------------------------------------


NOMINATIM_HIT = {
    "name": "University of Nairobi",
    "display_name": "University of Nairobi, Nairobi, Kenya",
    "lat": "-1.292065",
    "lon": "36.821945",
    "category": "amenity",
    "type": "university",
    "osm_id": 123,
    "osm_type": "relation",
    "amenity": "university",
    "address": {
        "city": "Nairobi",
        "country": "Kenya",
        "road": "University Way",
        "postcode": "00100",
    },
}


class TestPlaceSearch:
    """Real places, or none at all."""

    def test_normalises_a_real_hit(self):
        place = _normalise_place(NOMINATIM_HIT)
        assert place["name"] == "University of Nairobi"
        assert place["latitude"] == pytest.approx(-1.292065)
        assert place["longitude"] == pytest.approx(36.821945)
        assert place["locality"] == "Nairobi"
        assert place["street"] == "University Way"

    def test_place_without_coordinates_is_dropped(self):
        """No coordinates means no directions, so it is not worth showing."""
        assert _normalise_place({"name": "Nowhere", "lat": "", "lon": ""}) is None
        assert _normalise_place({"name": "Nowhere"}) is None

    def test_place_without_a_name_is_dropped(self):
        assert _normalise_place({"lat": "1.0", "lon": "2.0"}) is None

    def test_optional_fields_are_absent_rather_than_invented(self):
        place = _normalise_place(NOMINATIM_HIT)
        assert "phone" not in place, "no phone may be invented"
        assert "openingHours" not in place

    def test_returns_real_results(self, groq_http):
        groq_http(lambda r: httpx.Response(200, json=[NOMINATIM_HIT], request=r))
        result = run(PlaceSearchTool(settings()).run({"query": "University of Nairobi"}))
        assert result.ok is True
        assert result.data["resultCount"] == 1
        assert result.data["places"][0]["name"] == "University of Nairobi"

    def test_no_match_is_an_honest_empty_result(self, groq_http):
        """Not found must be empty, never padded with a guessed place."""
        groq_http(lambda r: httpx.Response(200, json=[], request=r))
        result = run(PlaceSearchTool(settings()).run({"query": "zzzznotaplace"}))
        assert result.ok is True
        assert result.data["resultCount"] == 0
        assert result.data["places"] == []
        assert result.data["message"]

    def test_missing_query_fails_cleanly(self):
        result = run(PlaceSearchTool(settings()).run({"query": "  "}))
        assert result.ok is False

    def test_rate_limit_is_reported_not_hidden(self, groq_http):
        groq_http(lambda r: httpx.Response(429, json={}, request=r))
        result = run(PlaceSearchTool(settings()).run({"query": "hospital"}))
        assert result.ok is False
        assert "rate limited" in (result.error or "").lower()

    def test_request_identifies_the_application(self, groq_http):
        """Nominatim's policy requires a real, identifying User-Agent."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ua"] = request.headers.get("user-agent", "")
            return httpx.Response(200, json=[NOMINATIM_HIT], request=request)

        groq_http(handler)
        run(PlaceSearchTool(settings()).run({"query": "university of nairobi"}))
        assert "SautiPay" in seen["ua"]
        assert "Mozilla" not in seen["ua"], "must not masquerade as a browser"


# ----------------------------------------------------------------------
# Map links
# ----------------------------------------------------------------------


class TestSourceLinks:
    """Links are built only from verified coordinates."""

    def test_builds_three_targets(self):
        links = build_links(-1.2921, 36.8219, name="University of Nairobi")
        assert set(links) == {"openstreetmap", "googleMaps", "appleMaps"}
        assert "-1.2921" in links["googleMaps"]

    def test_null_island_is_rejected(self):
        """A confident link to the middle of the ocean is worse than none."""
        assert build_links(0.0, 0.0) == {}

    def test_out_of_range_is_rejected(self):
        assert build_links(91.0, 0.0) == {}
        assert build_links(0.0, 181.0) == {}

    def test_garbage_is_rejected(self):
        assert build_links("abc", "def") == {}
        assert build_links(None, None) == {}

    def test_places_without_coordinates_are_skipped(self):
        tool = SourceLinksTool(settings())
        result = run(
            tool.run(
                {
                    "places": [
                        {"name": "Good", "latitude": -1.29, "longitude": 36.82},
                        {"name": "Bad", "locality": "Nairobi"},
                    ]
                }
            )
        )
        assert result.ok is True
        assert result.data["resultCount"] == 1
        assert result.data["skipped"] == 1
        assert "links" in result.data["linked"][0]

    def test_empty_input_fails_cleanly(self):
        result = run(SourceLinksTool(settings()).run({"places": []}))
        assert result.ok is False


# ----------------------------------------------------------------------
# Cards
# ----------------------------------------------------------------------


class TestCards:
    """Four distinct, render-ready card types."""

    def test_place_card(self):
        cards = cards_from_results([
            ToolResult(tool="place_search", ok=True, data={"places": [{
                "name": "University of Nairobi", "latitude": -1.29, "longitude": 36.82,
                "locality": "Nairobi", "country": "Kenya", "street": "University Way",
            }]})
        ])
        assert len(cards) == 1
        assert cards[0]["type"] == "place"
        assert cards[0]["data"]["name"] == "University of Nairobi"
        assert cards[0]["data"]["latitude"] == pytest.approx(-1.29)

    def test_place_card_requires_coordinates(self):
        cards = cards_from_results([
            ToolResult(tool="place_search", ok=True, data={"places": [{"name": "Nowhere"}]})
        ])
        assert cards == []

    def test_product_card(self):
        cards = cards_from_results([
            ToolResult(tool="search_marketplace", ok=True, data={"results": [{
                "name": "Maize 90kg", "price_minor": 620000, "currency": "KES",
                "vendorName": "Meru Valley", "location": "Meru", "verified": True,
            }]})
        ])
        assert cards[0]["type"] == "product"
        assert cards[0]["data"]["priceMinor"] == 620000
        assert cards[0]["data"]["vendor"] == "Meru Valley"

    def test_product_card_prefers_the_formatted_price(self):
        """The marketplace sends a formatted label; it must win.

        Its raw priceMinor is whole shillings, not cents, so a client that
        divided by 100 would render KSh 14,500 as KSh 145.
        """
        cards = cards_from_results([
            ToolResult(tool="search_marketplace", ok=True, data={"results": [{
                "name": "Yanza beans", "price": "KSh 14,500",
                "priceMinor": 14500, "currency": "KES", "vendorName": "Meru Valley",
            }]})
        ])
        data = cards[0]["data"]
        assert data["priceLabel"] == "KSh 14,500"
        assert data["priceMinor"] == 14500, "raw amount still passed through"

    def test_product_card_flattens_a_nested_vendor(self):
        """The marketplace nests the vendor; the card must flatten it."""
        cards = cards_from_results([
            ToolResult(tool="search_marketplace", ok=True, data={"results": [{
                "name": "Maize", "isVendorVerified": True,
                "images": ["https://cdn.example/maize.jpg"],
                "vendor": {
                    "id": "v1", "businessName": "Meru Valley", "slug": "meru-valley",
                    "phone": "+254700111003", "location": "Meru", "rating": 4.7,
                },
            }]})
        ])
        data = cards[0]["data"]
        assert data["vendor"] == "Meru Valley"
        assert data["vendorSlug"] == "meru-valley"
        assert data["vendorPhone"] == "+254700111003"
        assert data["verified"] is True
        assert data["image"] == "https://cdn.example/maize.jpg"
        assert data["rating"] == 4.7

    def test_study_card(self):
        cards = cards_from_results([
            ToolResult(tool="study_generator", ok=True, data={
                "topic": "Human Anatomy", "level": "secondary",
                "overview": "Body structures.",
                "keyConcepts": [{"term": "Heart", "explanation": "Pumps blood."}],
                "notes": ["n1"],
                "practiceQuestions": [{"question": "Q?", "answer": "A", "explanation": "e"}],
                "furtherReading": ["Gray's Anatomy"],
            })
        ])
        assert cards[0]["type"] == "study"
        assert cards[0]["data"]["topic"] == "Human Anatomy"
        assert cards[0]["data"]["practiceQuestions"][0]["question"] == "Q?"

    def test_business_card(self):
        cards = cards_from_results([
            ToolResult(tool="business_advisor", ok=True, data={
                "business": "Cereal shop", "goal": "More customers",
                "targetCustomers": ["Mothers"], "marketing": ["WhatsApp group"],
                "pricing": ["KSh 120/kg"],
                "sevenDayPlan": [{"day": "Day 1", "action": "Visit suppliers"}],
                "thirtyDayPlan": ["Add a stall"],
            })
        ])
        assert cards[0]["type"] == "business"
        assert len(cards[0]["data"]["sevenDayPlan"]) == 1

    def test_failed_tool_produces_no_card(self):
        cards = cards_from_results([
            ToolResult(tool="place_search", ok=False, error="rate limited")
        ])
        assert cards == []

    def test_duplicates_are_suppressed_in_either_order(self):
        place = ToolResult(tool="place_search", ok=True, data={"places": [
            {"name": "UoN", "latitude": -1.29, "longitude": 36.82}]})
        links = ToolResult(tool="source_links", ok=True, data={"linked": [
            {"name": "UoN", "latitude": -1.29, "longitude": 36.82,
             "links": {"googleMaps": "https://maps.google.com/?q=x"}}]})
        for results in ([place, links], [links, place]):
            cards = cards_from_results(results)
            assert len(cards) == 1
            assert cards[0]["data"]["links"]["googleMaps"]

    def test_card_count_is_bounded(self):
        many = ToolResult(tool="place_search", ok=True, data={"places": [
            {"name": f"P{i}", "latitude": -1.0 - i / 1000, "longitude": 36.0}
            for i in range(40)]})
        assert len(cards_from_results([many])) <= 8

    def test_cards_are_json_serialisable(self):
        cards = cards_from_results([
            ToolResult(tool="place_search", ok=True, data={"places": [
                {"name": "UoN", "latitude": -1.29, "longitude": 36.82}]})
        ])
        assert json.loads(json.dumps(cards)) == cards


# ----------------------------------------------------------------------
# Request schema
# ----------------------------------------------------------------------


class TestRequestSchema:
    """The new request fields."""

    def test_mode_defaults_to_internet(self):
        assert SautiChatRequest(message="hi").mode == "internet"

    def test_modes_are_validated(self):
        for mode in ("internet", "education", "business"):
            assert SautiChatRequest(message="hi", mode=mode).mode == mode
        with pytest.raises(ValueError):
            SautiChatRequest(message="hi", mode="cooking")

    def test_location_is_trimmed_and_bounded(self):
        assert SautiChatRequest(message="hi", user_location="  Kisumu  ").user_location == "Kisumu"
        assert SautiChatRequest(message="hi", user_location="   ").user_location is None

    def test_oversized_location_is_rejected(self):
        """Rejected rather than silently truncated: a partial place is a
        location the user never typed."""
        with pytest.raises(ValueError):
            SautiChatRequest(message="hi", user_location="x" * 500)

    def test_history_defaults_to_empty(self):
        assert SautiChatRequest(message="hi").history == []

    def test_original_fields_still_work(self):
        payload = SautiChatRequest(message="hi", language="sw", use_tools=False)
        assert payload.language == "sw" and payload.use_tools is False


class TestModeBiasIsNotALock:
    """Regression cover for bias that overrode a clear intent.

    An earlier version added the mode bonus to favoured intents even when
    they had no keyword evidence, which let Business mode drag "find me a
    hospital" to business_advice. A mode may tilt a decision between
    plausible readings; it must never manufacture a candidate.
    """

    @pytest.mark.parametrize(
        "message,intent",
        [
            ("find me a hospital near me", Intent.PLACE_SEARCH),
            ("find restaurants near Westlands", Intent.PLACE_SEARCH),
            ("what is the price of maize", Intent.PRICE_QUERY),
        ],
    )
    @pytest.mark.parametrize("mode", ["internet", "education", "business"])
    def test_clear_intent_survives_every_mode(self, message, intent, mode):
        router = IntentRouter(Settings(groq_api_key=""))
        result = run(router.route(message, mode=mode, request_id="lock"))
        assert result.intent is intent, (
            f"{message!r} in {mode} mode routed to {result.intent.value}"
        )

    def test_bias_only_touches_intents_with_evidence(self):
        from backend.agent.intent_router import MODE_AFFINITY

        scores = {Intent.PLACE_SEARCH: 0.8}
        router = IntentRouter(Settings(groq_api_key=""))
        adjusted = router._apply_bias(scores, "business")
        for intent in MODE_AFFINITY["business"]:
            assert intent not in adjusted, (
                f"{intent.value} was invented by the mode with no evidence"
            )

    def test_bias_still_favours_a_real_candidate(self):
        """The bias must still do its job when the evidence exists."""
        router = IntentRouter(Settings(groq_api_key=""))
        scores = {Intent.PRODUCT_SEARCH: 0.8, Intent.MARKETING: 1.3}
        biased = router._apply_bias(scores, "business")
        assert max(biased.values()) == pytest.approx(1.3 + MODE_BIAS)
        assert max(biased, key=biased.get) is Intent.MARKETING


class TestPlaceSearchCountryFallback:
    """Regression: the country must not be prepended destructively.

    Appending ", Kenya" to every query destroyed recall for queries that
    already named a place, so "restaurants near Westlands" found five places
    while the same query with the country appended found none. The country is
    now only a fallback for a query that found nothing.
    """

    def test_query_is_sent_unchanged_first(self, groq_http):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json=[NOMINATIM_HIT], request=request)

        groq_http(handler)
        run(
            PlaceSearchTool(settings()).run(
                {"query": "restaurants near Westlands", "country": "Kenya"}
            )
        )
        assert seen == ["restaurants near Westlands"], (
            "the user's phrasing must be tried verbatim first"
        )

    def test_country_is_appended_only_after_an_empty_result(self, groq_http):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json=[], request=request)

        groq_http(handler)
        run(
            PlaceSearchTool(settings()).run(
                {"query": "somewhere obscure", "country": "Kenya"}
            )
        )
        assert seen == ["somewhere obscure", "somewhere obscure, Kenya"]

    def test_no_second_call_when_the_first_succeeds(self, groq_http):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params.get("q", ""))
            return httpx.Response(200, json=[NOMINATIM_HIT], request=request)

        groq_http(handler)
        run(
            PlaceSearchTool(settings()).run(
                {"query": "restaurants near Westlands", "country": "Kenya"}
            )
        )
        assert len(seen) == 1, "no wasted call when the first query worked"

    def test_a_failed_lookup_is_distinct_from_no_match(self, groq_http):
        """A 500 is an outage, not proof the place does not exist."""
        groq_http(lambda r: httpx.Response(500, json={}, request=r))
        result = run(
            PlaceSearchTool(settings()).run({"query": "hospital", "country": "Kenya"})
        )
        assert result.ok is False
        assert "unavailable" in (result.error or "").lower()


class TestPlaceLinksAreIncluded:
    """Map links come from place_search itself, not a second model call."""

    def test_places_carry_links_without_asking(self, groq_http):
        groq_http(lambda r: httpx.Response(200, json=[NOMINATIM_HIT], request=r))
        result = run(PlaceSearchTool(settings()).run({"query": "University of Nairobi"}))
        links = result.data["places"][0]["links"]
        assert set(links) == {"openstreetmap", "googleMaps", "appleMaps"}

    def test_links_appear_on_the_card(self, groq_http):
        groq_http(lambda r: httpx.Response(200, json=[NOMINATIM_HIT], request=r))
        result = run(PlaceSearchTool(settings()).run({"query": "University of Nairobi"}))
        cards = cards_from_results([result])
        assert cards[0]["data"]["links"]["googleMaps"]

    def test_no_second_model_call_is_needed(self, groq_http):
        """The whole point: links must not depend on the model cooperating."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=[NOMINATIM_HIT], request=request)

        groq_http(handler)
        result = run(PlaceSearchTool(settings()).run({"query": "University of Nairobi"}))
        assert result.data["places"][0].get("links")
        # Only the Nominatim call was made; no source_links round-trip.
        assert calls["n"] == 1


class TestCardStringSectionsSurvive:
    """Regression: plain-string sections were silently dropped.

    The card builder looked values up with a dict helper, so passing a bare
    string found no keys and produced None. A study card lost its notes and
    further reading, and a business card lost its marketing and pricing,
    with no error raised anywhere.
    """

    def test_study_card_keeps_notes_and_reading(self):
        cards = cards_from_results([
            ToolResult(tool="study_generator", ok=True, data={
                "topic": "Photosynthesis", "level": "secondary",
                "notes": ["Light-dependent reactions use photosystem II."],
                "furtherReading": ["Campbell Biology, Chapter 10"],
                "practiceQuestions": [],
            })
        ])
        data = cards[0]["data"]
        assert data["notes"] == ["Light-dependent reactions use photosystem II."]
        assert data["furtherReading"] == ["Campbell Biology, Chapter 10"]

    def test_business_card_keeps_every_string_section(self):
        cards = cards_from_results([
            ToolResult(tool="business_advisor", ok=True, data={
                "business": "Cereal shop",
                "targetCustomers": ["Mothers in Nyamira"],
                "marketing": ["Join the local WhatsApp group"],
                "pricing": ["KSh 120 per kilo"],
                "thirtyDayPlan": ["Add a second stall"],
                "sevenDayPlan": [{"day": "Day 1", "action": "Visit suppliers"}],
            })
        ])
        data = cards[0]["data"]
        assert data["targetCustomers"] == ["Mothers in Nyamira"]
        assert data["marketing"] == ["Join the local WhatsApp group"]
        assert data["pricing"] == ["KSh 120 per kilo"]
        assert data["thirtyDayPlan"] == ["Add a second stall"]
        assert data["sevenDayPlan"][0]["action"] == "Visit suppliers"

    def test_seven_day_plan_accepts_plain_strings(self):
        cards = cards_from_results([
            ToolResult(tool="business_advisor", ok=True, data={
                "business": "Shop",
                "sevenDayPlan": ["Visit five suppliers"],
            })
        ])
        assert cards[0]["data"]["sevenDayPlan"][0]["action"] == "Visit five suppliers"

    def test_blank_and_non_string_entries_are_dropped(self):
        cards = cards_from_results([
            ToolResult(tool="study_generator", ok=True, data={
                "topic": "T", "notes": ["", "   ", "real note", 42, None],
            })
        ])
        assert cards[0]["data"]["notes"] == ["real note"]

    def test_text_helper_rejects_non_strings(self):
        from backend.agent.cards import _text

        assert _text("ok") == "ok"
        assert _text("") is None
        assert _text(None) is None
        assert _text(123) is None


def final_answer(text: str) -> dict:
    """A chat-completions response carrying a final answer."""
    return {
        "model": "qwen/qwen3.8-27b",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _registry_with(*tools):
    """A real ToolRegistry seeded with the given tools."""
    from backend.tools.registry import ToolRegistry

    reg = ToolRegistry(settings())
    for tool in tools:
        reg.register(tool)
    return reg


class TestOneCallPerTool:
    """Generative tools are not re-run once they have answered."""

    def test_a_second_identical_call_is_skipped(self, groq_http):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if any("TOOL RESULTS" in str(m.get("content", "")) for m in body["messages"]):
                return httpx.Response(200, json=final_answer("done"), request=request)
            return httpx.Response(
                200,
                json={
                    "model": "qwen/qwen3.8-27b",
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {"id": f"c{i}", "type": "function",
                                 "function": {"name": "study_generator",
                                              "arguments": json.dumps({"topic": "anatomy"})}}
                                for i in range(2)
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5},
                },
                request=request,
            )

        groq_http(handler)

        from backend.agent.groq_loop import run_groq_turn
        from backend.tools.study_generator import StudyGeneratorTool

        async def fake_study(self, arguments):
            return ToolResult(tool="study_generator", ok=True, data={"topic": "anatomy"})

        StudyGeneratorTool.run = fake_study
        try:
            turn = run(
                run_groq_turn(
                    settings=settings(sauti_query_rewriting=False, sauti_self_verification=False),
                    registry=_registry_with(StudyGeneratorTool(settings())),
                    system="S",
                    user_text="give me revision questions",
                )
            )
            assert len(turn.tool_results) == 1, "the generator must run once per turn"
        finally:
            del StudyGeneratorTool.run


class TestFollowUpToolScoping:
    """A follow-up must be able to act on the material it recalled.

    "Make them harder" matches no intent keyword, so it routes to general_chat,
    which is tool-free by design. With earlier material in context the model
    would then be structurally unable to regenerate it.
    """

    def test_small_talk_stays_tool_free(self):
        assert allowed_tools(Intent.GENERAL_CHAT) == ()

    def test_follow_up_keeps_the_generating_tools(self):
        tools = allowed_tools(Intent.GENERAL_CHAT, has_artifacts=True)
        assert "study_generator" in tools
        assert "business_advisor" in tools

    def test_unchanged_when_no_artifacts(self):
        assert allowed_tools(Intent.PLACE_SEARCH) == allowed_tools(Intent.PLACE_SEARCH, has_artifacts=True)

    def test_never_duplicates_a_tool(self):
        tools = allowed_tools(Intent.STUDY_MATERIAL, has_artifacts=True)
        assert len(tools) == len(set(tools))
