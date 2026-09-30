"""Hybrid marketplace search.

Combines four independent signals and reports which ones contributed, so a
score is explainable rather than manufactured:

  1. lexical / alias similarity  - token overlap plus alias expansion
  2. fuzzy similarity            - character-level closeness of model names
  3. structured attribute match  - colour, horsepower, engine, price range
  4. contextual match            - location, availability, vendor verification

A vector (pgvector) stage plugs in behind `VectorIndex` when a real embedding
model and PostgreSQL are available. The ranking below works without either.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..db import db
from .models import Product, Vendor
from .query import ParsedQuery, parse_query

logger = logging.getLogger(__name__)

#: Weights for the hybrid score. Tunable, and reported in the result.
WEIGHTS = {
    "lexical": 0.40,
    "fuzzy": 0.20,
    "attributes": 0.25,
    "context": 0.15,
}

#: Horsepower tolerance when the user said "around 600".
HORSEPOWER_TOLERANCE = 0.15


class VectorIndex:
    """Vector similarity stage.

    Intentionally inert until a real embedding model plus pgvector exist.
    `enabled` is False in that case, and the hybrid score does not pretend to
    include a vector component.
    """

    def __init__(self) -> None:
        self.enabled = False
        self.reason = (
            "No embedding model configured and/or no pgvector column available. "
            "Search is using lexical, fuzzy and structured matching only."
        )

    def similarity(self, query_vector: list[float] | None, product) -> float | None:
        return None


vector_index = VectorIndex()


@dataclass
class ScoredProduct:
    product: Product
    score: float
    components: dict = field(default_factory=dict)
    matched: list = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = self.product.to_dict()
        payload["matchScore"] = round(self.score, 4)
        payload["matchComponents"] = {k: round(v, 4) for k, v in self.components.items()}
        payload["matchedOn"] = self.matched
        vendor = self.product.vendor
        payload["vendor"] = {
            "id": vendor.id,
            "businessName": vendor.business_name,
            "slug": vendor.slug,
            "location": vendor.location,
            "isVerified": vendor.is_verified,
            "rating": vendor.rating,
            "logo": vendor.logo_url,
            "phone": vendor.phone,
            "deliveryAvailable": vendor.delivery_available,
            "acceptsMpesa": vendor.accepts_mpesa,
        } if vendor else None
        return payload


def _text_of(product: Product) -> str:
    parts = [product.name or "", product.description or ""]
    parts.extend(str(t) for t in (product.tags or []))
    for pair in product.spec_pairs():
        parts.append(f"{pair['label']} {pair['value']}")
    for pair in product.attribute_pairs():
        parts.append(f"{pair['label']} {pair['value']}")
    if product.vendor:
        parts.append(product.vendor.business_name)
    return " ".join(parts).lower()


def _lexical_score(query: ParsedQuery, product: Product) -> tuple[float, list[str]]:
    """Score direct keywords precisely, then add a smaller alias bonus.

    Alias expansion must not dilute precision: a product containing the exact
    words the user typed should outrank one that merely shares a category word.
    """
    haystack = _text_of(product)
    if not haystack:
        return 0.0, []

    direct = list(dict.fromkeys(query.keywords))
    if not direct:
        return 0.0, []

    matched: list[str] = []

    def _hits(terms: list[str]) -> list[str]:
        return [
            term
            for term in terms
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack)
        ]

    direct_hits = _hits(direct)
    # Alias terms the user did not type, e.g. "maharagwe" -> "beans",
    # "maïs" -> "maize". A query whose only term is a foreign synonym
    # ("prix du maïs") must still match, so alias-only hits are scored too.
    extra = [t for t in query.expanded_terms if t not in set(direct)]
    alias_hits = _hits(extra)

    if not direct_hits:
        # Alias-only match: real, but weaker than the user's own words.
        if not alias_hits:
            return 0.0, []
        return min(0.62, 0.30 + 0.10 * len(alias_hits)), alias_hits[:4]

    # Precision of the user's own words: longer words carry more signal.
    hit_weight = sum(min(len(t), 12) for t in direct_hits)
    total_weight = sum(min(len(t), 12) for t in direct) or 1
    direct_score = hit_weight / total_weight
    matched.extend(direct_hits)

    alias_score = min(1.0, len(alias_hits) / 4.0) if extra else 0.0
    matched.extend(alias_hits[:3])

    return min(1.0, 0.78 * direct_score + 0.22 * alias_score), matched


def _fuzzy_score(query: ParsedQuery, product: Product) -> float:
    """Character-level closeness, so 'G-Wagon' reaches 'G63 AMG'."""
    best = 0.0
    name = (product.name or "").lower()
    if not name:
        return 0.0

    # Compare the query's model phrase against the product name and its tags.
    candidates = [name] + [str(t).lower() for t in (product.tags or [])]
    model_terms = [
        term for term in query.expanded_terms
        if any(alias in term or term in alias for alias in
               ("g-wagon", "gwagon", "g63", "g-class", "land cruiser", "range rover"))
    ]
    if not model_terms:
        model_terms = [t for t in query.keywords if len(t) > 3][:3]
    if not model_terms:
        return 0.0

    for term in model_terms:
        normalised_term = term.replace("-", "").replace(" ", "")
        for candidate in candidates:
            normalised_candidate = candidate.replace("-", "").replace(" ", "")
            best = max(best, SequenceMatcher(None, normalised_term, normalised_candidate).ratio())
    return best


def _attribute_score(query: ParsedQuery, product: Product) -> tuple[float, list[str]]:
    """Exact structured matches. Returns 1.0 only when every stated filter hits."""
    checks: list[bool] = []
    matched: list[str] = []

    if query.color:
        values = " ".join(
            str(v).lower() for v in (product.attributes or {}).values()
        ) + " " + " ".join(str(v).lower() for v in (product.specifications or {}).values())
        hit = query.color in values
        checks.append(hit)
        if hit:
            matched.append(f"colour: {query.color}")

    if query.horsepower:
        raw = (product.attributes or {}).get("horsepower") or (
            product.specifications or {}
        ).get("horsepower") or (product.specifications or {}).get("engine")
        found = None
        if raw is not None:
            digits = re.search(r"\d{2,4}", str(raw))
            if digits:
                found = int(digits.group(0))
        if found is not None:
            spread = max(1.0, found * HORSEPOWER_TOLERANCE)
            hit = abs(found - query.horsepower) <= spread
            checks.append(hit)
            if hit:
                matched.append(f"~{query.horsepower} HP")

    if query.engine_cc:
        raw = (product.specifications or {}).get("engine_cc")
        found = None
        if raw is not None:
            digits = re.search(r"\d{3,4}", str(raw))
            if digits:
                found = int(digits.group(0))
        if found is not None:
            hit = found == query.engine_cc
            checks.append(hit)
            if hit:
                matched.append(f"{query.engine_cc} cc")

    if query.max_price_minor is not None and product.price_minor is not None:
        hit = product.price_minor <= query.max_price_minor
        checks.append(hit)
        if hit:
            matched.append("within budget")

    if query.min_price_minor is not None and product.price_minor is not None:
        hit = product.price_minor >= query.min_price_minor
        checks.append(hit)
        if hit:
            matched.append("above minimum price")

    if not checks:
        return 0.0, matched
    return sum(checks) / len(checks), matched


def _context_score(query: ParsedQuery, product: Product) -> tuple[float, list[str]]:
    """Location, availability and vendor verification."""
    checks: list[bool] = []
    matched: list[str] = []
    vendor = product.vendor

    if query.location:
        product_location = (product.location or (vendor.location if vendor else "") or "").lower()
        served = vendor.serves(query.location) if vendor else None
        if query.location.lower() in product_location:
            checks.append(True)
            matched.append(f"located in {query.location}")
        elif served is True:
            checks.append(True)
            matched.append(f"delivers to {query.location}")
        else:
            checks.append(False)

    if query.only_available:
        checks.append(product.is_available)
        if product.is_available:
            matched.append("available")

    if query.only_verified and vendor is not None:
        checks.append(vendor.is_verified)
        if vendor.is_verified:
            matched.append("verified vendor")

    if not checks:
        return 0.0, matched
    return sum(checks) / len(checks), matched


def _category_filter(query: ParsedQuery) -> str | None:
    return query.category


def search_products(
    query: str | ParsedQuery,
    limit: int = 20,
    category: str | None = None,
    include_unavailable: bool = True,
    min_score: float = 0.15,
) -> tuple[list[ScoredProduct], ParsedQuery, dict]:
    """Run hybrid search over active products.

    Returns:
        (scored_results, parsed_query, diagnostics)
    """
    parsed = parse_query(query) if isinstance(query, str) else query
    target_category = category or parsed.category

    products = (
        db.session.query(Product)
        .join(Vendor, Product.vendor_id == Vendor.id)
        .filter(Product.is_active.is_(True))
        .limit(2000)
        .all()
    )

    scored: list[ScoredProduct] = []
    for product in products:
        if target_category and (product.category or "").lower() != target_category:
            # A product in another category can still match if the user named
            # it explicitly, so only skip when the product contradicts.
            if parsed.keywords and _lexical_score(parsed, product)[0] > 0.5:
                pass
            else:
                continue

        if not include_unavailable and not product.is_available:
            continue

        lexical, lex_hits = _lexical_score(parsed, product)
        fuzzy = _fuzzy_score(parsed, product)
        if lexical == 0 and fuzzy == 0 and parsed.keywords:
            # Nothing matched lexically or by name similarity: not relevant.
            continue

        attributes, attr_matched = _attribute_score(parsed, product)
        context, ctx_matched = _context_score(parsed, product)

        components = {
            "lexical": lexical,
            "fuzzy": fuzzy,
            "attributes": attributes,
            "context": context,
        }
        score = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)

        # A hard "only_available" or "only_verified" request must not be
        # satisfied by merely ranking an unavailable item highly.
        if query_only_available(parsed) and not product.is_available:
            score = 0.0
        if parsed.only_verified and product.vendor and not product.vendor.is_verified:
            score = 0.0

        if score < min_score:
            continue

        scored.append(
            ScoredProduct(
                product=product,
                score=score,
                components=components,
                matched=lex_hits[:4] + attr_matched + ctx_matched,
            )
        )

    scored.sort(key=lambda item: item.score, reverse=True)
    scored = scored[:limit]

    diagnostics = {
        "vectorEnabled": vector_index.enabled,
        "vectorNote": vector_index.reason,
        "weights": WEIGHTS,
        "candidatesConsidered": len(products),
        "results": len(scored),
    }
    return scored, parsed, diagnostics


def query_only_available(parsed: ParsedQuery) -> bool:
    return parsed.only_available


def popular_searches(limit: int = 8) -> list[dict]:
    """Popular searches from real recorded search events only."""
    from sqlalchemy import func

    from .models import SearchEvent

    rows = (
        db.session.query(
            SearchEvent.normalised_query,
            func.count(SearchEvent.id).label("runs"),
            func.sum(SearchEvent.result_count).label("hits"),
        )
        .filter(SearchEvent.had_results.is_(True))
        .group_by(SearchEvent.normalised_query)
        .order_by(func.count(SearchEvent.id).desc())
        .limit(limit)
        .all()
    )
    return [
        {"query": q, "searches": int(runs or 0), "resultCount": int(hits or 0)}
        for q, runs, hits in rows
        if q
    ]
