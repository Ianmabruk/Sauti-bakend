"""Intent classification for SAUTI.

Routing happens in two stages, cheapest first:

1. **Rules** decide the obvious cases. This is a pure regex pass with no
   network cost, which matters because the default free tier meters every
   request. It is also the only stage that reliably handles Kiswahili and
   French, where a small English-trained model tends to drift.
2. **The model** is consulted only when the rules are inconclusive, using the
   cheap fast-model tier at temperature 0.

The sidebar mode is applied as a *bias*, never a lock: it adds to the score of
the intents that mode favours, so "where is the nearest hospital" still finds
a hospital while the user is in Business mode.

Every path degrades to ``GENERAL_CHAT`` rather than raising. A router that
fails must not take the turn down with it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..config.settings import Settings

logger = logging.getLogger(__name__)

#: How much a matching sidebar mode shifts an intent's score.
MODE_BIAS = 2.0

#: Below this score the rules are considered inconclusive and the model is asked.
RULE_CONFIDENCE = 1.0


class Intent(str, Enum):
    """Every intent SAUTI can route to."""

    GENERAL_CHAT = "general_chat"
    LOCATION_SEARCH = "location_search"
    PLACE_SEARCH = "place_search"
    PRODUCT_SEARCH = "product_search"
    VENDOR_SEARCH = "vendor_search"
    SERVICE_SEARCH = "service_search"
    EDUCATION = "education"
    STUDY_MATERIAL = "study_material"
    CODING = "coding"
    ENGINEERING = "engineering"
    BUSINESS_ADVICE = "business_advice"
    MARKETING = "marketing"
    PRICE_QUERY = "price_query"
    DIRECTIONS = "directions"
    GENERAL_INFORMATION = "general_information"


#: Which intents each sidebar mode nudges towards.
MODE_AFFINITY: dict[str, tuple[Intent, ...]] = {
    "education": (Intent.EDUCATION, Intent.STUDY_MATERIAL, Intent.CODING, Intent.ENGINEERING),
    "business": (Intent.BUSINESS_ADVICE, Intent.MARKETING, Intent.PRICE_QUERY),
    "internet": (),
}

#: Keywords per intent. English, Kiswahili and French are listed because the
#: rules are the only stage that must not depend on the model handling all
#: three languages reliably.
INTENT_KEYWORDS: dict[Intent, tuple[str, ...]] = {
    Intent.PLACE_SEARCH: (
        # Deliberately no "find a" / "find me a" here: that phrasing is shared
        # with shopping ("find me a blue Mercedes") and cannot identify a
        # place. The category words below are unambiguous.
        "university", "universities", "hospital", "hospitals",
        "restaurant", "restaurants", "college", "clinic", "clinics",
        "supermarket", "mall", "museum", "library", "pharmacy",
        "primary school", "secondary school", "police station", "post office",
        "hospitali", "shule", "chuo", "restauranti", "hoteli",
        "supermarche", "hopital", "ecole", "pharmacie",
    ),
    Intent.DIRECTIONS: (
        "directions", "how do i get", "how to get there", "route", "distance",
        "nearest", "how far", "direction", "barabara", "njia", "karibu na",
    ),
    Intent.LOCATION_SEARCH: (
        "location", "located", "where is", "where are", "find nearby",
        "around me", "in my area", "mtaa", "eneko",
    ),
    Intent.PRODUCT_SEARCH: (
        "product", "products", "item", "buy", "purchase", "shop", "for sale",
        "in stock", "cheapest", "bidhaa", "ununuzi", "nunua", "natafuta",
        "tafuta", "produit", "acheter", "à acheter",
        # Specification words are a reliable product signal: a request carrying
        # them is shopping even when it opens with "find me", which is
        # otherwise a place-shaped phrase.
        "horsepower", "engine", "transmission", "automatic", "litre", "litres",
    ),
    Intent.VENDOR_SEARCH: (
        "vendor", "vendors", "seller", "supplier", "business", "shop owner",
        "company", "who sells", "wauzaji", "muuzaji", "msambaji", "kampuni",
        "vendeur", "fournisseur",
    ),
    Intent.SERVICE_SEARCH: (
        "service", "services", "repair", "plumber", "electrician", "cleaning",
        "huduma", "matengenezo", "usafi",
    ),
    Intent.PRICE_QUERY: (
        "price", "prices", "cost", "how much", "bei", "bei ya", "soko",
        "prix", "combien", "rate", "market price",
    ),
    Intent.STUDY_MATERIAL: (
        "revision", "revision questions", "practice questions", "quiz", "past paper",
        "exam questions", "flashcards", "study material", "revision notes",
        "mazoezi", "maswali", "mitihani", "kuandaa",
    ),
    Intent.EDUCATION: (
        "study", "studying", "school", "university", "course", "learn",
        "explain", "understand", "student", "teacher", "learning",
        "kujifunza", "masomo", "mtihani", "apprendre", "etudier",
    ),
    Intent.CODING: (
        "python", "javascript", "java", "code", "coding", "program", "function",
        "bug", "error", "algorithm", "html", "css", "react", "sql", "debug",
        "programu", "hitilafu", "code",
    ),
    Intent.ENGINEERING: (
        "engineering", "thermodynamics", "mechanical", "electrical", "civil",
        "structural", "circuit", "fluid dynamics", "mechanics", "uhandisi",
    ),
    Intent.BUSINESS_ADVICE: (
        "business", "start a business", "start my", "business plan", "shop plan",
        "advice for my", "how do i start", "grow my", "help me start",
        "running my", "my business", "bidhaa biashara", "kuanza biashara",
        "kwa biashara yangu", "conseils", "entreprise", "ouvrir un commerce",
    ),
    Intent.MARKETING: (
        "marketing", "market my", "advertise", "promote", "get customers",
        "reach customers", "social media", "campaign", "brand", "sell more",
        "uuzaji", "kampeni", "wanunuzi wengi", "publicite", "faire connaître",
    ),
    Intent.GENERAL_CHAT: (
        "hello", "hi ", "hey", "thanks", "thank you", "how are you", "who are you",
        "jambo", "hujumba", "asante", "shukrani", "bonjour", "salut", "merci",
    ),
}

#: Phrases that mean the user wants a place, not a thing to buy. Checked before
#: the generic place words so "find a hospital supplier" leans to a place.
_PLACE_HINTS = re.compile(
    r"\b(find|locate|search for|show me|where is|where are|nearest|nearby|"
    r"kuta|tafuta|where|ou se trouve|trouver)\b",
    re.IGNORECASE,
)

#: A place-shaped request is a POI, not a shop listing.
_PLACE_HINT_PLACE = re.compile(
    r"\b(find|locate|search for|show me|where is|where are|nearest|nearby|"
    r"kuta|tafuta|ou se trouve|trouver)\b",
    re.IGNORECASE,
)

#: A structured place is a POI, not a shop listing.
_PLACE_CATEGORY = re.compile(
    r"\b(university|universities|college|school|hospital|clinic|restaurant|"
    r"cafe|hotel|bank|supermarket|mall|museum|library|pharmacy|police|"
    r"hospitali|shule|chuo|restauranti|hoteli|banKI|duka la|supermarket)\b",
    re.IGNORECASE,
)

#: Longer, more specific phrases are stronger evidence than a single common
#: word. Without this, "how do I market my shop" ties on the word "shop" and
#: loses to the shopping intent it is not really about.
def normalise_text(text: str) -> str:
    """Fold diacritics so accented languages match the keyword lists.

    Users type "hôpital", "maïs" and "école" with accents while the keyword
    lists are written in plain ASCII. Without this fold those phrases match
    nothing and every French query falls through to the model.
    """
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _phrase_weight(keyword: str) -> float:
    """Score contribution for one matching keyword."""
    words = keyword.strip().split()
    if len(words) >= 3:
        return 1.6
    if len(words) == 2:
        return 1.3
    return 0.8


_COMPILED: dict[Intent, re.Pattern] = {
    intent: re.compile("|".join(re.escape(k) for k in words), re.IGNORECASE)
    for intent, words in INTENT_KEYWORDS.items()
}


@dataclass
class IntentResult:
    """A routing decision.

    Attributes:
        intent: The winning intent.
        confidence: 0.0-1.0. Rule matches are strong; a model guess is not.
        scores: Score per candidate, for logging and debugging.
        source: ``"rules"``, ``"model"`` or ``"fallback"``.
        reason: Short explanation.
        mode: The mode that was applied, if any.
    """

    intent: Intent = Intent.GENERAL_CHAT
    confidence: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)
    source: str = "fallback"
    reason: str = ""
    mode: str = ""

    @property
    def is_fallback(self) -> bool:
        """True when nothing matched and the default was assumed."""
        return self.source == "fallback"

    def to_dict(self) -> dict:
        """A JSON-safe view for the response body."""
        return {
            "intent": self.intent.value,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "reason": self.reason,
            "mode": self.mode or None,
        }


#: Which tools each intent may use.
#:
#: This is a scoping hint, not a lock: the registry still validates whatever
#: the model asks for. Its real purpose is token economy. Eleven always-on
#: tool schemas add roughly 800 tokens to every single request, and the free
#: provider tier meters input tokens per minute, so showing a student only the
#: study tool is both cheaper and more accurate.
INTENT_TOOLS: dict[Intent, tuple[str, ...]] = {
    Intent.PLACE_SEARCH: ("place_search", "source_links", "web_search", "web_reader", "search_marketplace"),
    Intent.DIRECTIONS: ("place_search", "source_links", "web_search"),
    Intent.LOCATION_SEARCH: ("place_search", "source_links", "web_search", "web_reader"),
    Intent.PRODUCT_SEARCH: ("search_marketplace", "get_product_details", "get_vendor_profile", "web_search"),
    Intent.VENDOR_SEARCH: ("search_marketplace", "get_vendor_profile", "get_product_details", "web_search"),
    Intent.SERVICE_SEARCH: ("search_marketplace", "get_vendor_profile", "web_search"),
    Intent.PRICE_QUERY: ("search_marketplace", "web_search", "get_product_details"),
    Intent.STUDY_MATERIAL: ("study_generator", "web_search"),
    Intent.EDUCATION: ("study_generator", "web_search", "web_reader"),
    Intent.CODING: ("web_search", "web_reader", "calculator"),
    Intent.ENGINEERING: ("study_generator", "web_search", "calculator"),
    Intent.BUSINESS_ADVICE: ("business_advisor", "search_marketplace", "web_search"),
    Intent.MARKETING: ("business_advisor", "search_marketplace", "web_search"),
    Intent.GENERAL_CHAT: (),
    Intent.GENERAL_INFORMATION: ("web_search", "web_reader", "calculator"),
}

#: Tools offered when the router could not decide. Deliberately broad, because
#: a wrong guess here should look like the pre-router behaviour.
FALLBACK_TOOLS: tuple[str, ...] = (
    "search_marketplace", "get_vendor_profile", "get_product_details",
    "search_news", "place_search", "source_links", "study_generator",
    "business_advisor", "web_search", "web_reader", "calculator",
)


#: Tools that stay available when the turn clearly continues earlier work.
#:
#: A follow-up like "make them harder" or "more questions" matches no keyword,
#: so it routes to general_chat, which by design offers no tools at all. That
#: would leave the model unable to act on the material it just generated, even
#: though that material has been recalled into its context. When earlier
#: material is in play, these tools are kept so the follow-up can be served.
FOLLOW_UP_TOOLS: tuple[str, ...] = ("study_generator", "business_advisor")


def allowed_tools(intent: Intent, *, has_artifacts: bool = False) -> tuple[str, ...]:
    """Which tools an intent should be offered.

    Args:
        intent: The routed intent.
        has_artifacts: True when earlier generated study material or a business
            plan was recalled for this turn. Follow-up phrasing such as "make
            them harder" carries no intent of its own, so the tools that can
            extend the recalled material are kept available.

    Returns:
        Tool names to pass to the model. Empty for small talk, where the
        correct behaviour is to answer without looking anything up.
    """
    tools = INTENT_TOOLS.get(intent, FALLBACK_TOOLS)
    if not has_artifacts:
        return tools
    if intent is Intent.GENERAL_CHAT:
        # The ambiguous follow-up case, and the only one that needs this.
        # Any other intent already carries the tools its own answer requires,
        # and adding a study generator to a hospital question would only
        # inflate the prompt.
        return FOLLOW_UP_TOOLS
    return tools


def normalise_mode(mode: Optional[str]) -> str:
    """Coerce an incoming mode to a known key, defaulting to internet.

    Args:
        mode: Raw mode from the client.

    Returns:
        One of ``internet``, ``education`` or ``business``.
    """
    candidate = (mode or "").strip().lower()
    return candidate if candidate in MODE_AFFINITY else "internet"


def score_by_rules(message: str) -> dict[Intent, float]:
    """Score every intent from keyword evidence.

    A place-shaped request is deliberately demoted from the shopping intents:
    "find a hospital near me" matches PRODUCT_SEARCH's "find me a", which
    would otherwise send a hospital query to the marketplace.

    Args:
        message: The user's message.

    Returns:
        A mapping of intent to score; intents with no evidence are omitted.
    """
    text = normalise_text(message)
    if not text:
        return {}

    scores: dict[Intent, float] = {}
    place_shaped = bool(_PLACE_HINT_PLACE.search(text))

    for intent, words in INTENT_KEYWORDS.items():
        pattern = _COMPILED[intent]
        matches = pattern.findall(text)
        if not matches:
            continue
        # findall returns the whole match for a non-grouping alternation.
        weight = min(3.0, sum(_phrase_weight(m) for m in matches))
        if place_shaped and intent in {
            Intent.PRODUCT_SEARCH,
            Intent.VENDOR_SEARCH,
            Intent.SERVICE_SEARCH,
        } and not _PLACE_CATEGORY.search(text):
            # A place request that only matched a shopping keyword.
            weight *= 0.4
        scores[intent] = weight

    return scores


_MODEL_PROMPT = """\
You classify one user message for a Kenyan AI assistant.

Reply with a single JSON object and nothing else:
{"intent": "<one of the listed intents>", "reason": "<max 12 words>"}

Intents:
{intent_list}

Rules:
- STUDY_MATERIAL when the user wants practice questions, revision, a quiz or
  study notes.
- PRICE_QUERY when they want the price of something.
- PLACE_SEARCH when they want a real-world venue or institution such as a
  university, hospital, restaurant or shop location.
- PRODUCT_SEARCH when they want to buy a product Sauti sells.
- VENDOR_SEARCH when they want a supplier or business.
- DIRECTIONS when they ask how to get somewhere or how far it is.
- CODING for programming help, ENGINEERING for engineering subjects.
- BUSINESS_ADVICE for starting or running a business, MARKETING for
  promoting one.
- GENERAL_CHAT for greetings and small talk.
- GENERAL_INFORMATION for stable factual questions.
- GENERAL_CHAT only when nothing else fits.

Message: {message}
"""


class IntentRouter:
    """Classifies a message into an :class:`Intent`.

    Args:
        settings: Runtime settings, used to reach the cheap model tier.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        from ..config.settings import get_settings

        self.settings = settings or get_settings()

    async def route(
        self,
        message: str,
        *,
        mode: Optional[str] = None,
        user_location: Optional[str] = None,
        request_id: str = "-",
    ) -> IntentResult:
        """Classify one message.

        Args:
            message: The user's message.
            mode: Sidebar mode, applied as a soft bias.
            user_location: Optional explicit location, added as context for
                the model pass only. It never overrides what the user wrote.
            request_id: Correlation id for logging.

        Returns:
            An :class:`IntentResult`. Never raises.
        """
        text = (message or "").strip()
        active_mode = normalise_mode(mode)
        if not text:
            return IntentResult(source="fallback", reason="empty message", mode=active_mode)

        scores = score_by_rules(text)
        ruled = self._apply_bias(scores, active_mode)
        best = max(ruled.items(), key=lambda kv: kv[1], default=None)

        if best and best[1] >= RULE_CONFIDENCE:
            result = IntentResult(
                intent=best[0],
                confidence=min(1.0, best[1] / 3.0),
                scores={k.value: round(v, 2) for k, v in sorted(ruled.items(), key=lambda kv: -kv[1])[:4]},
                source="rules",
                reason=f"keyword match for {best[0].value}",
                mode=active_mode,
            )
            logger.info(
                "rid=%s intent=%s confidence=%.2f source=rules mode=%s",
                request_id, result.intent.value, result.confidence, active_mode,
            )
            return result

        return await self._route_with_model(
            text, ruled, active_mode, user_location, request_id
        )

    def _apply_bias(self, scores: dict[Intent, float], mode: str) -> dict[Intent, float]:
        """Add the mode's affinity bonus to the candidates that exist.

        The bias is only ever applied to intents the rules already found some
        evidence for. Adding it unconditionally would let a mode invent a
        candidate out of nothing, and with a bonus larger than a normal
        keyword score that turns the bias into a lock: in Business mode,
        "find me a hospital" would be dragged to business_advice despite
        having no business keywords at all. A mode may tilt a decision between
        plausible readings, never manufacture one.
        """
        favoured = MODE_AFFINITY.get(mode, ())
        if not favoured:
            return scores
        adjusted = dict(scores)
        for intent in favoured:
            if intent in adjusted:
                adjusted[intent] += MODE_BIAS
        return adjusted

    async def _route_with_model(
        self,
        message: str,
        ruled: dict[Intent, float],
        mode: str,
        user_location: Optional[str],
        request_id: str,
    ) -> IntentResult:
        """Ask the cheap model when the rules are inconclusive."""
        intent_list = "\n".join(f"- {i.value}" for i in Intent)
        location_hint = f"\nThe user's location: {user_location}" if user_location else ""
        prompt = (
            _MODEL_PROMPT.replace("{intent_list}", intent_list)
            .replace("{message}", message + location_hint)
        )

        guessed = None
        try:
            from ..integrations.groq import GroqClient

            client = GroqClient(self.settings)
            if client.configured:
                raw = await client.complete_text(prompt, temperature=0.0, max_tokens=120)
                guessed = _parse_intent(raw)
        except Exception as exc:  # noqa: BLE001 - routing must never break a turn
            logger.info("rid=%s intent model pass failed: %s", request_id, type(exc).__name__)

        scores = dict(ruled)
        if guessed is not None:
            scores[guessed] = scores.get(guessed, 0.0) + 3.0

        scored = self._apply_bias(scores, mode)
        best = max(scored.items(), key=lambda kv: kv[1], default=None)

        if best is None:
            return IntentResult(
                source="fallback", reason="no signal", mode=mode
            )

        # A model guess is trusted less than a rule match.
        confidence = 0.8 if guessed is not None and best[0] == guessed else 0.4
        result = IntentResult(
            intent=best[0],
            confidence=confidence,
            scores={k.value: round(v, 2) for k, v in sorted(scored.items(), key=lambda kv: -kv[1])[:4]},
            source="model" if guessed is not None and best[0] == guessed else "rules",
            reason="model classification" if guessed is not None and best[0] == guessed else "fallback after model pass",
            mode=mode,
        )
        logger.info(
            "rid=%s intent=%s confidence=%.2f source=%s mode=%s",
            request_id, result.intent.value, result.confidence, result.source, mode,
        )
        return result


def _parse_intent(raw: str) -> Optional[Intent]:
    """Pull a valid intent out of a model reply, or return None."""
    if not raw:
        return None
    import json

    start = raw.find("{")
    if start == -1:
        return None
    end = raw.rfind("}")
    if end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    value = str(payload.get("intent") or "").strip().lower()
    for intent in Intent:
        if intent.value == value:
            return intent
    return None
