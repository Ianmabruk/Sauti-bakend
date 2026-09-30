"""Sauti query understanding.

Turns free-form natural language into a structured `ParsedQuery`. This runs
before any search runs, so the marketplace is queried with real constraints
rather than a raw `LIKE '%...%'`.

Supports English, Kiswahili and French, including code-switched input such as
"Natafuta mtu anauza maharagwe ya Yanza, mimi niko Samburu."
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

#: Words that carry no search intent.
_STOPWORDS = {
    "i", "want", "need", "looking", "look", "for", "find", "me", "a", "an",
    "the", "please", "show", "get", "any", "some", "there", "is", "are",
    "to", "of", "in", "on", "at", "with", "and", "or", "can", "you", "do",
    "have", "has", "sauti", "na", "ninahitaji", "tafuta", "tafuta", "nipe",
    "niko", "nataka", "kwa", "ya", "wa", "ni", "katika", "hapo", "yake",
    "je", "wapi", "bei", "gani", "ununuzi", "nunua", "sauti",
    "je", "un", "une", "des", "de", "du", "la", "le", "les", "un", "une",
    "je", "voudrais", "cherche", "trouve", "ou", "avec", "pour", "s'il",
    "please", "how", "much", "what", "is", "are", "do", "you", "have",
}

#: Product-category keyword families. First match wins for a given family.
CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "vehicles": ("car", "cars", "vehicle", "vehicles", "auto", "automobile",
                 "gari", "magari", "basi", "truck", "lorry", "motorcycle",
                 "voiture", "véhicule", "berlin", "mercedes", "bmw", "toyota",
                 "nissan", "honda", "range rover", "land cruiser"),
    "agriculture": ("maize", "corn", "beans", "maharagwe", "wheat", "ngano",
                    "rice", "mchele", "potato", "viazi", "tomato", "nyanya",
                    "seed", "seeds", "mbegu", "fertilizer", "kilimo",
                    "agriculture", "farming", "harvest", "crop", "livestock",
                    "horticulture", "maji", "mafumbuka",
                    "maïs", "haricot", "haricots", "riz", "blé", "tomate",
                    "pomme de terre", "pommes de terre", "semence", "récolte"),
    "real_estate": ("house", "flat", "apartment", "plot", "land", "property",
                    "nyumba", "shamba", "ardhi", "immobilier", "maison",
                    "appartement", "terrain"),
    "jobs": ("job", "jobs", "work", "employment", "vacancy", "kazi",
             "nafasi", "emploi", "travail"),
    "electronics": ("laptop", "phone", "smartphone", "tv", "television",
                    "camera", "laptopu", "simu", "runinga", "électronique",
                    "ordinateur"),
    "fashion": ("dress", "shoe", "shoes", "clothes", "fashion", "ngeu",
                "vatu", "mgao", "nazi", "mode", "vêtement"),
    "food": ("food", "meal", "restaurant", "chakula", "nyama", "mboga",
             "matunda", "mafruits", "nourriture", "cuisine"),
    "travel": ("flight", "ticket", "hotel", "travel", "safiri", "hotel",
               "voyage", "billet", "vol"),
    "health": ("doctor", "hospital", "clinic", "medicine", "daktari",
               "hospitali", "médicament", "médecin"),
    "construction": ("cement", "sand", "steel", "roof", "bento", "mchanga",
                     "simba", "construction", "béton"),
    "services": ("service", "services", "huduma", "repair", "plumber",
                 "electrician", "service"),
}

#: Vehicle model families and the words that mean the same thing to a buyer.
VEHICLE_ALIASES: dict[str, tuple[str, ...]] = {
    "g-wagon": ("g-wagon", "gwagon", "g wagon", "g-class", "g class", "gclass",
                "mercedes g", "g63", "g 63", "g500", "g 500"),
    "gle": ("gle", "g le", "gle-class", "suv coupe"),
    "glc": ("glc", "g lc"),
    "land-cruiser": ("land cruiser", "landcruiser", "land-cruiser", "v8", "prado"),
    "range-rover": ("range rover", "rangerover", "ranger over", "autobiography"),
    "defender": ("land rover defender", "defender 110", "defender 90"),
    "patrol": ("nissan patrol", "patrol y61"),
    "hilux": ("toyota hilux", "hilux"),
    "suv": ("suv", "4x4", "4 by 4", "off road", "off-road"),
}

#: Colour families.
COLOR_ALIASES: dict[str, tuple[str, ...]] = {
    "blue": ("blue", "bluish", "navy", "bluu", "bleu", "azur"),
    "black": ("black", "nyeusi", "noir"),
    "white": ("white", "nyeupe", "blanc"),
    "silver": ("silver", "chrome", "kioo", "argent"),
    "red": ("red", "nyekundu", "rouge"),
    "grey": ("grey", "gray", "kijivu", "gris"),
    "green": ("green", "kijani", "vert"),
    "gold": ("gold", "dhahabu", "doré", "or"),
}

#: Places Sauti can reason about without a geocoder.
KNOWN_PLACES: dict[str, str] = {
    "nairobi": "Nairobi", "nairobii": "Nairobi",
    "mombasa": "Mombasa", "kisumu": "Kisumu", "nakuru": "Nakuru",
    "eldoret": "Eldoret", "thika": "Thika", "kitale": "Kitale",
    "meru": "Meru", "kakamega": "Kakamega", "nyeri": "Nyeri", "kisii": "Kisii",
    "garissa": "Garissa", "narok": "Narok", "busia": "Busia", "siaya": "Siaya",
    "malindi": "Malindi", "naivasha": "Naivasha", "emburu": "Embu",
    "kericho": "Kericho", "machakos": "Machakos", "kiambu": "Kiambu",
    "samburu": "Samburu", "maralal": "Maralal", "isiolo": "Isiolo",
    "nyahururu": "Nyahururu", "kajiado": "Kajiado", "athi river": "Athi River",
}

_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)
_NUMBER_RE = re.compile(r"(\d[\d,.]*)\s*(k|hp|horsepower|cc|l|litres|kg|km)?", re.I)
_PRICE_RE = re.compile(
    r"(?:ksh|kes|kshs\.?|shillingi)\s*([\d,]+(?:\.\d+)?)\s*"
    r"(k|m|mn|million)?",
    re.I,
)


@dataclass
class ParsedQuery:
    """A structured interpretation of one natural-language search."""

    raw: str
    category: str | None = None
    keywords: list[str] = field(default_factory=list)
    expanded_terms: list[str] = field(default_factory=list)
    vehicle_model: str | None = None
    color: str | None = None
    horsepower: int | None = None
    engine_cc: int | None = None
    location: str | None = None
    max_price_minor: int | None = None
    min_price_minor: int | None = None
    only_available: bool = False
    only_verified: bool = False
    quantity: int | None = None
    text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _normalise_place(token: str) -> str | None:
    return KNOWN_PLACES.get(token.lower().strip())


def _find_in_text(text: str, aliases: dict[str, tuple[str, ...]]) -> str | None:
    lowered = text.lower()
    best_key = None
    best_len = 0
    for key, words in aliases.items():
        for word in words:
            # Word-boundary match so "suv" does not fire inside another word.
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered):
                if len(word) > best_len:
                    best_key, best_len = key, len(word)
    return best_key


def parse_query(text: str) -> ParsedQuery:
    """Parse a free-form request into structured constraints.

    Every field is derived from the actual input. Nothing is guessed: if a
    constraint is not stated it stays None rather than being defaulted.
    """
    raw = (text or "").strip()
    lowered = raw.lower()
    tokens = _tokens(raw)

    query = ParsedQuery(raw=raw, text=raw)

    # --- category ---
    query.category = _find_in_text(lowered, CATEGORY_ALIASES)

    # --- vehicle model + colour ---
    query.vehicle_model = _find_in_text(lowered, VEHICLE_ALIASES)
    query.color = _find_in_text(lowered, COLOR_ALIASES)

    # --- numeric constraints ---
    for amount, unit in _NUMBER_RE.findall(raw):
        value = float(amount.replace(",", ""))
        unit_key = (unit or "").lower()
        if unit_key in {"hp", "horsepower"}:
            query.horsepower = int(value)
        elif unit_key == "cc":
            query.engine_cc = int(value)

    if query.horsepower is None:
        # "around 600 hp" / "about 600 horsepower" also appear as bare numbers
        # next to a vehicle term.
        if query.category == "vehicles" or query.vehicle_model:
            bare = re.search(r"(?:around|about|approximately|circa|~|approx)\s*(\d{2,4})", lowered)
            if bare:
                query.horsepower = int(bare.group(1))

    # --- location ---
    for token in tokens:
        place = _normalise_place(token)
        if place:
            query.location = place
            break
    if query.location is None:
        for phrase in ("samburu", "nairobi", "meru"):
            if phrase in lowered:
                query.location = KNOWN_PLACES[phrase]
                break

    # --- price ---
    price_match = _PRICE_RE.search(lowered)
    if price_match:
        amount = float(price_match.group(1).replace(",", ""))
        multiplier = (price_match.group(2) or "").lower()
        if multiplier in {"k"}:
            amount *= 1_000
        elif multiplier in {"m", "mn", "million"}:
            amount *= 1_000_000
        query.max_price_minor = int(amount)
    if re.search(r"\b(?:under|below|less than|maximum|max|cheaper than)\b", lowered):
        if query.max_price_minor is not None:
            pass  # already set by the price pattern
    elif re.search(r"\b(?:over|above|more than|minimum|min|at least)\b", lowered):
        if query.max_price_minor is not None:
            query.min_price_minor = query.max_price_minor
            query.max_price_minor = None

    # --- availability / verification intent ---
    if re.search(
        r"\b(?:available|in stock|ready|for sale|on sale|na available|ipo|wazi)\b", lowered
    ):
        query.only_available = True
    if re.search(r"\b(?:verified|trusted|legit|authentic|uhakiki)\b", lowered):
        query.only_verified = True

    # --- quantity ---
    qty = re.search(r"\b(\d{1,4})\s*(?:units?|pieces?|pcs|pieces)\b", lowered)
    if qty:
        query.quantity = int(qty.group(1))

    # --- keywords and expansion ---
    meaningful = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
    seen: set[str] = set()
    query.keywords = [t for t in meaningful if not (t in seen or seen.add(t))]

    expanded = set(query.keywords)
    if query.category and query.category in CATEGORY_ALIASES:
        expanded.update(CATEGORY_ALIASES[query.category])
    if query.vehicle_model and query.vehicle_model in VEHICLE_ALIASES:
        expanded.update(VEHICLE_ALIASES[query.vehicle_model])
    if query.color and query.color in COLOR_ALIASES:
        expanded.update(COLOR_ALIASES[query.color])
    query.expanded_terms = sorted(expanded)

    return query


def is_discovery_request(text: str) -> bool:
    """True when the user wants to find a vendor/product, not have a chat.

    Deliberately broad: when in doubt, treat it as a discovery request so the
    marketplace is consulted rather than the model answering from memory.
    """
    lowered = (text or "").lower()
    if is_news_request(text):
        return False
    discovery_markers = (
        "find", "looking for", "need", "want", "search", "show me", "where can i",
        "who sells", "available", "for sale", "price of", "buy", "get me",
        "tafuta", "ninataka", "ninahitaji", "nipe", "anzia", "bei ya",
        "auza", "anauza", "anapatika", "ipo", "napata",
        "chercher", "trouve", "je veux", "ou trouver", "acheter", "vendre",
    )
    if any(marker in lowered for marker in discovery_markers):
        return True

    parsed = parse_query(text)
    # A product noun plus a place is a search even without an explicit verb.
    return bool(parsed.category and (parsed.location or parsed.keywords))


#: Requests for news specifically. Kept separate from discovery so a news
#: question reads Sauti's newsroom instead of falling through to web search.
_NEWS_MARKERS = (
    "news", "habari", "magazeti", "latest news", "news stories", "headlines", "what's happening",
    "whats happening", "breaking", "today's news", "top stories",
    "habari", "magazeti", "habari za leo", "makala",
    "actualites", "actualités", "nouvelles", "les nouvelles", "dernières nouvelles",
)


def is_news_request(text: str) -> bool:
    """True when the user is asking for news rather than a product."""
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _NEWS_MARKERS):
        # A product noun plus a news word is still a product search.
        parsed = parse_query(text)
        return not bool(parsed.category)
    return False
