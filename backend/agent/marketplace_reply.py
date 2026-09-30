"""Marketplace-aware grounded responses.

Used when no language model is configured. Composes an answer strictly from
what the marketplace tool actually returned, and refuses to invent a vendor,
price, phone number or availability when nothing was found.
"""
from __future__ import annotations

from typing import Optional

NO_RESULT = {
    "en": "I couldn't find a matching vendor in the current marketplace.",
    "sw": "Sikupata muuzaji unaofanana sokoni ya sasa.",
    "fr": "Je n'ai pas trouvé de vendeur correspondant sur la place de marché actuelle.",
}

EMPTY_SEARCH = {
    "en": "I couldn't search the marketplace right now. Please try again in a moment.",
    "sw": "Sikuweza kutafuta sokoni kwa sasa. Tafadhali jaribu tena baadaye.",
    "fr": "Je n'ai pas pu interroger la place de marché pour le moment. Réessayez.",
}

HEADER = {
    "en": "I found {count} matching {noun} in the marketplace.",
    "sw": "Nimepata {count} {noun} zinazolingana sokoni.",
    "fr": "J'ai trouvé {count} {noun} correspondants sur la place de marché.",
}

NOUN = {
    "en": {"one": "listing", "many": "listings"},
    "sw": {"one": "orodha", "many": "orodha"},
    "fr": {"one": "annonce", "many": "annonces"},
}

CAVEAT = {
    "en": "Prices, availability and location come directly from each vendor's listing.",
    "sw": "Bei, upatikanaji na eneo vinatoka moja kwa moja kwenye orodha ya muuzaji.",
    "fr": "Les prix, la disponibilité et la localisation proviennent des fiches vendeurs.",
}

EMPTY_BODY = {
    "en": (
        "I searched Sauti's own vendor database and there is no matching listing "
        "right now. I won't make one up. Try widening the search — a different "
        "town, a nearby category, or fewer specifications."
    ),
    "sw": (
        "Nilitafuta katika hifadhi ya wauzaji wa Sauti na hakuna orodha inayolingana "
        "kwa sasa. Sitatunga moja. Jaribu kupanua utafutaji — mji mwingine, kategoria "
        "inayolingana, au vichungo vichache."
    ),
    "fr": (
        "J'ai cherché dans la base des vendeurs de Sauti : aucune annonce "
        "correspondante pour le moment. Je n'en inventerai pas. Essayez d'élargir "
        "la recherche : une autre ville, une catégorie voisine, moins de critères."
    ),
}

NUDGE = {
    "en": "Want me to check another area or relax a specification?",
    "sw": "Ungependa nikagueze eneo lingine au kulegeza kiwango?",
    "fr": "Souhaitez-vous que je vérifie une autre zone ou que j'assouplisse un critère ?",
}


def compose_marketplace_reply(
    tool_result,
    language: str = "en",
    query: str = "",
) -> str:
    """Build an honest answer from a `search_marketplace` tool result."""
    lang = language if language in NO_RESULT else "en"

    if not tool_result.ok:
        detail = tool_result.error or "unknown error"
        return f"{EMPTY_SEARCH[lang]}\n\nTechnical detail: {detail}"

    data = tool_result.data or {}
    results = data.get("results") or []

    if not results:
        message = data.get("message") or NO_RESULT[lang]
        lines = [message, "", EMPTY_BODY[lang]]
        if query:
            lines.append(f"\nSearch: “{query}”")
        return "\n".join(lines)

    count = len(results)
    noun = NOUN[lang]["one"] if count == 1 else NOUN[lang]["many"]
    lines = [HEADER[lang].format(count=count, noun=noun), ""]

    for item in results[:5]:
        vendor = item.get("vendor") or {}
        name = item.get("name", "Unnamed listing")
        business = vendor.get("businessName") or "Unverified vendor"
        price = item.get("price") or "price not stated"
        location = item.get("location") or vendor.get("location") or "location not stated"
        available = "available" if item.get("isAvailable") else "currently unavailable"
        verified = "verified" if vendor.get("isVerified") else "not verified"

        specs = item.get("specifications") or []
        spec_text = ", ".join(f"{s['label']}: {s['value']}" for s in specs[:4])

        lines.append(f"• {name} — {business} ({verified})")
        lines.append(f"   {price} · {location} · {available}")
        if spec_text:
            lines.append(f"   {spec_text}")
        if item.get("id"):
            lines.append(f"   id: {item['id']}")
        lines.append("")

    lines.append(CAVEAT[lang])
    lines.append(NUDGE[lang])
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

NEWS_HEADER = {
    "en": "Here are the latest stories Sauti has published:",
    "sw": "Haya kuna hadithi za hivi karibuni ambazo Sauti imechapisha:",
    "fr": "Voici les dernières histoires publiées par Sauti :",
}

NEWS_EMPTY = {
    "en": "I couldn't find any published news for that right now.",
    "sw": "Sikupata habari yoyote iliyochapishwa kwa swali hilo kwa sasa.",
    "fr": "Je n'ai trouvé aucune actualité publiée pour ce sujet pour le moment.",
}

NEWS_LIVE = {
    "en": (
        "For breaking news right now I would search the wider web — "
        "Sauti's newsroom only holds articles that have been published."
    ),
    "sw": (
        "Kwa habari za leo za moja kwa moja ningewatafuta mtandao mpana — "
        "vipindi vya Sauti vinashikilia makala zilizochapishwa tu."
    ),
    "fr": (
        "Pour l'actualité en direct, je chercherais le web au sens large — "
        "la rédaction de Sauti ne contient que les articles publiés."
    ),
}


def compose_news_reply(tool_result, language: str = "en") -> str:
    """Build an honest answer from Sauti's own newsroom rows."""
    lang = language if language in NEWS_HEADER else "en"

    if not tool_result.ok:
        return f"{NEWS_EMPTY[lang]}\n\nTechnical detail: {tool_result.error or 'unknown'}"

    articles = (tool_result.data or {}).get("articles") or []
    if not articles:
        return f"{NEWS_EMPTY[lang]}\n\n{NEWS_LIVE[lang]}"

    lines = [NEWS_HEADER[lang], ""]
    for article in articles[:5]:
        date = (article.get("publishedAt") or "")[:10] or "date not stated"
        lines.append(f"• {article.get('title', 'Untitled')}")
        if article.get("summary"):
            lines.append(f"   {article['summary']}")
        source = article.get("source") or "Sauti"
        lines.append(f"   {article.get('category', 'general')} · {source} · {date}")
        if article.get("id"):
            lines.append(f"   id: {article['id']}")
        lines.append("")

    lines.append(NEWS_LIVE[lang])
    return "\n".join(lines).strip()
