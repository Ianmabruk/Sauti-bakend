"""Pluggable web-search providers.

SAUTI is not bound to one search engine. A provider is selected by the
``SEARCH_PROVIDER`` environment variable:

    auto      pick the first configured provider (default)
    brave     Brave Search API   (SEARCH_API_KEY)
    tavily    Tavily API         (SEARCH_API_KEY)
    serpapi   SerpAPI            (SEARCH_API_KEY)
    duckduckgo  keyless HTML endpoint, frequently blocked by anti-bot
    stub      deterministic offline provider, for tests only

Credentials are read from the environment and never returned to callers.
"""
from __future__ import annotations

import abc
import asyncio
import json
import logging
import re
import time
from typing import Optional
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import httpx

from ...config.settings import Settings
from ..citations import Source, classify_source, domain_of

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36"
)


class SearchUnavailable(RuntimeError):
    """No search provider is usable right now."""


class SearchProvider(abc.ABC):
    """Base class for search providers."""

    name: str = "base"
    requires_key: bool = True

    def __init__(self, settings: Settings):
        self.settings = settings

    def is_available(self) -> bool:
        """Whether this provider can be used with current settings."""
        return bool(self.settings.search_api_key) if self.requires_key else True

    @abc.abstractmethod
    async def search(self, query: str, max_results: int = 8) -> list[Source]:
        """Run a search and return normalised Source objects."""


class BraveSearchProvider(SearchProvider):
    """Brave Search API. Requires SEARCH_API_KEY."""

    name = "brave"
    requires_key = True
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    async def search(self, query: str, max_results: int = 8) -> list[Source]:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.settings.search_api_key,
        }
        params = {"q": query, "count": min(max_results, 20)}

        async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
            response = await self._request_with_retry(client, self.endpoint, headers, params)
            response.raise_for_status()
            payload = response.json()

        results = (payload.get("web") or {}).get("results", [])
        sources = []
        for item in results[:max_results]:
            url = item.get("url", "")
            sources.append(
                Source(
                    title=(item.get("title") or "").strip(),
                    url=url,
                    domain=domain_of(url),
                    snippet=(item.get("description") or "").strip(),
                    published_at=(item.get("age") or None),
                    source_type=classify_source(url, item.get("title", "")),
                )
            )
        return _apply_relevance(sources, query)

    async def _request_with_retry(self, client, url, headers, params):
        last: Optional[Exception] = None
        for attempt in range(self.settings.search_retries + 1):
            try:
                return await client.get(url, headers=headers, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                if attempt < self.settings.search_retries:
                    await asyncio.sleep(0.4 * (attempt + 1))
        raise last if last else RuntimeError("search failed")


class TavilySearchProvider(SearchProvider):
    """Tavily search API. Requires SEARCH_API_KEY."""

    name = "tavily"
    requires_key = True
    endpoint = "https://api.tavily.com/search"

    async def search(self, query: str, max_results: int = 8) -> list[Source]:
        body = {
            "api_key": self.settings.search_api_key,
            "query": query,
            "max_results": min(max_results, 20),
            "search_depth": "basic",
        }
        async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
            response = await client.post(self.endpoint, json=body)
            response.raise_for_status()
            payload = response.json()

        sources = []
        for item in (payload.get("results") or [])[:max_results]:
            url = item.get("url", "")
            sources.append(
                Source(
                    title=(item.get("title") or "").strip(),
                    url=url,
                    domain=domain_of(url),
                    snippet=(item.get("content") or "").strip()[:600],
                    published_at=item.get("published_date") or None,
                    source_type=classify_source(url, item.get("title", "")),
                )
            )
        return _apply_relevance(sources, query)


class SerpApiProvider(SearchProvider):
    """SerpAPI. Requires SEARCH_API_KEY."""

    name = "serpapi"
    requires_key = True
    endpoint = "https://serpapi.com/search"

    async def search(self, query: str, max_results: int = 8) -> list[Source]:
        params = {
            "q": query,
            "api_key": self.settings.search_api_key,
            "num": min(max_results, 20),
            "engine": "google",
        }
        async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()
            payload = response.json()

        sources = []
        for item in (payload.get("organic_results") or [])[:max_results]:
            url = item.get("link", "")
            sources.append(
                Source(
                    title=(item.get("title") or "").strip(),
                    url=url,
                    domain=domain_of(url),
                    snippet=(item.get("snippet") or "").strip(),
                    published_at=item.get("date") or None,
                    source_type=classify_source(url, item.get("title", "")),
                )
            )
        return _apply_relevance(sources, query)


_RESULT_ANCHOR = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S
)
_RESULT_SNIPPET = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.I | re.S
)
_TAG = re.compile(r"<[^>]+>")


class OpenMeteoProvider(SearchProvider):
    """Direct no-key weather provider for location-aware forecast queries."""

    name = "open-meteo"
    requires_key = False
    geocoding_endpoint = "https://geocoding-api.open-meteo.com/v1/search"
    forecast_endpoint = "https://api.open-meteo.com/v1/forecast"

    async def search(self, query: str, max_results: int = 8) -> list[Source]:
        location = _extract_weather_location(query)
        if not location:
            raise SearchUnavailable(
                "No city or town was identified in the weather query. "
                "Try 'weather in Nairobi' or 'forecast for Mombasa'."
            )

        async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
            geocode = await client.get(
                self.geocoding_endpoint,
                params={"name": location, "count": 1, "language": "en", "format": "json"},
            )
            geocode.raise_for_status()
            payload = geocode.json()

        results = payload.get("results") or []
        if not results:
            raise SearchUnavailable(f"No weather data was found for '{location}'.")

        place = results[0]
        lat = place.get("latitude")
        lon = place.get("longitude")
        name = (place.get("name") or location).strip()
        country = (place.get("country") or "").strip()
        if lat is None or lon is None:
            raise SearchUnavailable(f"Weather data for '{location}' could not be resolved.")

        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m,precipitation",
            "hourly": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset",
            "forecast_days": min(max_results, 5) if max_results > 0 else 3,
            "timezone": "auto",
        }

        async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
            forecast = await client.get(self.forecast_endpoint, params=params)
            forecast.raise_for_status()
            weather = forecast.json()

        current = weather.get("current") or {}
        daily = weather.get("daily") or {}
        temps = daily.get("temperature_2m_max") or []
        mins = daily.get("temperature_2m_min") or []
        daily_names = daily.get("time") or []
        summary = _summarise_weather_data(current, daily, name, country)

        title = f"Weather forecast for {name}{', ' + country if country else ''}"
        source = Source(
            title=title,
            url="https://open-meteo.com/",
            domain="open-meteo.com",
            snippet=summary,
            published_at=None,
            relevance=1.0,
            source_type=classify_source("https://open-meteo.com/", title),
        )
        if not summary:
            source.snippet = f"Weather conditions for {name} were retrieved from Open-Meteo."
        return [source]


class DuckDuckGoProvider(SearchProvider):
    """Keyless DuckDuckGo HTML endpoint.

    This provider is frequently blocked by anti-bot challenges in datacenter
    and CI environments. When blocked it raises SearchUnavailable so the agent
    reports an honest failure rather than inventing results.
    """

    name = "duckduckgo"
    requires_key = False
    endpoint = "https://html.duckduckgo.com/html/"

    async def search(self, query: str, max_results: int = 8) -> list[Source]:
        params = {"q": query}
        headers = {"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

        async with httpx.AsyncClient(
            timeout=self.settings.search_timeout, follow_redirects=True
        ) as client:
            response = await client.get(self.endpoint, params=params, headers=headers)
            response.raise_for_status()
            html = response.text

        lowered = html.lower()
        if "challenge" in lowered or "unfortunately, bots" in lowered:
            raise SearchUnavailable(
                "DuckDuckGo returned an anti-bot challenge instead of results. "
                "Configure a web search API key (Brave or Tavily) to enable "
                "live research."
            )

        sources: list[Source] = []
        snippets = [
            _clean(_TAG.sub(" ", m.group(1))) for m in _RESULT_SNIPPET.finditer(html)
        ]
        for index, match in enumerate(_RESULT_ANCHOR.finditer(html)):
            url = _unwrap_ddg_url(match.group(1))
            if not url:
                continue
            title = _clean(_TAG.sub(" ", match.group(2)))
            sources.append(
                Source(
                    title=title,
                    url=url,
                    domain=domain_of(url),
                    snippet=snippets[index] if index < len(snippets) else "",
                    source_type=classify_source(url, title),
                )
            )
            if len(sources) >= max_results:
                break

        if not sources:
            raise SearchUnavailable(
                "DuckDuckGo returned no parseable results for this query."
            )
        return _apply_relevance(sources, query)


def _unwrap_ddg_url(href: str) -> str:
    """DuckDuckGo wraps results in a redirect; unwrap to the real target."""
    href = unquote(href.strip())
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        query = parse_qs(urlparse(href).query)
        target = query.get("uddg")
        if target:
            return unquote(target[0])
    return href if href.startswith("http") else ""


def _clean(text: str) -> str:
    import html as html_mod

    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


def _looks_like_weather_query(text: str) -> bool:
    lowered = (text or "").lower()
    markers = (
        "weather",
        "forecast",
        "temperature",
        "rain",
        "rainy",
        "cloudy",
        "sunny",
        "wind",
        "humidity",
        "tomorrow",
        "today",
        "meteo",
        "hali ya hewa",
    )
    return any(marker in lowered for marker in markers)


def _extract_weather_location(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return ""

    compact = re.sub(
        r"^(?:what(?:'s| is)|whats|tell me|please|hey|hi|hello|sauti)\s+",
        "",
        text,
        flags=re.I,
    )
    compact = re.sub(
        r"\b(?:weather|forecast|temperature|rain|rainy|humid(?:ity|ity)|wind|cloud|sunny|meteo|hali ya hewa)\b",
        "",
        compact,
        flags=re.I,
    )
    compact = re.sub(r"\s+", " ", compact).strip(" ,.-")

    patterns = [
        r"\b(?:in|for|at|near)\s+([A-Za-z][A-Za-z\s'-]{1,40})(?=\s*(?:today|tomorrow|now|this week|this month|forecast|weather|report|$))",
        r"\b(?:in|for|at|near)\s+([A-Za-z][A-Za-z\s'-]{1,40})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            location = match.group(1).strip(" ,.-")
            if location:
                return location

    if compact:
        return compact
    return text


def _summarise_weather_data(current: dict, daily: dict, name: str, country: str) -> str:
    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    wind = current.get("wind_speed_10m")
    precipitation = current.get("precipitation")
    weather_code = current.get("weather_code")
    high = (daily.get("temperature_2m_max") or [None])[0]
    low = (daily.get("temperature_2m_min") or [None])[0]
    code_label = _weather_code_label(weather_code)
    city = f"{name}{', ' + country if country else ''}"
    parts = [f"Current weather in {city}: {code_label}"]
    if temp is not None:
        parts.append(f"{temp}°C")
    if feels is not None:
        parts.append(f"feels like {feels}°C")
    if humidity is not None:
        parts.append(f"humidity {humidity}%")
    if wind is not None:
        parts.append(f"wind {wind} km/h")
    if precipitation is not None:
        parts.append(f"precipitation {precipitation} mm")
    if high is not None and low is not None:
        parts.append(f"high {high}°C / low {low}°C today")
    return "; ".join(parts)


def _weather_code_label(code):
    lookup = {
        0: "clear sky",
        1: "mainly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "foggy",
        48: "depositing rime fog",
        51: "light drizzle",
        53: "moderate drizzle",
        55: "dense drizzle",
        56: "light freezing drizzle",
        57: "dense freezing drizzle",
        61: "slight rain",
        63: "moderate rain",
        65: "heavy rain",
        66: "light freezing rain",
        67: "heavy freezing rain",
        71: "light snow",
        73: "moderate snow",
        75: "heavy snow",
        77: "snow grains",
        80: "light showers",
        81: "moderate showers",
        82: "violent showers",
        85: "light snow showers",
        86: "heavy snow showers",
        95: "thunderstorm",
        96: "thunderstorm with hail",
        99: "heavy thunderstorm with hail",
    }
    return lookup.get(code, "variable conditions")


class StubSearchProvider(SearchProvider):
    """Deterministic offline provider used by tests and demos.

    It never touches the network. It is explicitly not a real search engine.
    """

    name = "stub"
    requires_key = False

    FIXTURES = [
        {
            "title": "Kenya Agricultural Statistics - Maize market",
            "url": "https://www.example-agriculture.test/maize-market",
            "snippet": "Maize wholesale prices in Kenya averaged KSh 3,400 per 90kg bag in Nairobi.",
            "published_at": "2026-09-20",
        },
        {
            "title": "Eldoret grain market daily prices",
            "url": "https://www.example-markets.test/eldoret-maize",
            "snippet": "Eldoret maize traded at KSh 3,250 per bag, slightly below the Nairobi rate.",
            "published_at": "2026-09-21",
        },
        {
            "title": "Ministry of Agriculture market bulletin",
            "url": "https://www.example-gov.test/agri-bulletin",
            "snippet": "Official bulletin covering weekly wholesale commodity prices.",
            "published_at": "2026-09-18",
        },
    ]

    async def search(self, query: str, max_results: int = 8) -> list[Source]:
        await asyncio.sleep(0)
        terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
        scored: list[tuple[float, Source]] = []
        for fixture in self.FIXTURES:
            haystack = f"{fixture['title']} {fixture['snippet']}".lower()
            overlap = sum(1 for term in terms if term in haystack)
            if overlap == 0 and terms:
                continue
            score = overlap / max(len(terms), 1)
            url = fixture["url"]
            scored.append(
                (
                    score,
                    Source(
                        title=fixture["title"],
                        url=url,
                        domain=domain_of(url),
                        snippet=fixture["snippet"],
                        published_at=fixture["published_at"],
                        relevance=score,
                        source_type=classify_source(url, fixture["title"]),
                    ),
                )
            )
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [source for _, source in scored[:max_results]]


class FailingSearchProvider(SearchProvider):
    """Always fails. Used to prove the agent reports failures honestly."""

    name = "failing"
    requires_key = False

    async def search(self, query: str, max_results: int = 8) -> list[Source]:
        await asyncio.sleep(0)
        raise SearchUnavailable("Search backend is unreachable (simulated failure).")


PROVIDERS: dict[str, type[SearchProvider]] = {
    "brave": BraveSearchProvider,
    "tavily": TavilySearchProvider,
    "serpapi": SerpApiProvider,
    "open-meteo": OpenMeteoProvider,
    "duckduckgo": DuckDuckGoProvider,
    "stub": StubSearchProvider,
    "failing": FailingSearchProvider,
}

#: Preference order used when SEARCH_PROVIDER=auto.
AUTO_ORDER = ("brave", "tavily", "serpapi", "duckduckgo", "open-meteo")


def build_search_provider(settings: Settings, query: str | None = None) -> SearchProvider:
    """Select a search provider based on settings.

    Raises:
        SearchUnavailable: When no provider can be used.
    """
    requested = (settings.search_provider or "auto").lower()
    weather_query = bool(query and _looks_like_weather_query(query))

    if requested != "auto":
        provider_cls = PROVIDERS.get(requested)
        if provider_cls is None:
            raise SearchUnavailable(f"Unknown SEARCH_PROVIDER: {requested}")
        provider = provider_cls(settings)
        if not provider.is_available():
            raise SearchUnavailable(
                f"Search provider '{requested}' requires a web search API key."
            )
        if weather_query and requested in {"auto", "duckduckgo"} and not settings.search_api_key:
            provider = OpenMeteoProvider(settings)
        logger.info("SEARCH PROVIDER selected=%s", provider.name)
        return provider

    if weather_query and not settings.search_api_key:
        logger.info("SEARCH PROVIDER selected=open-meteo (weather query without API key)")
        return OpenMeteoProvider(settings)

    for name in AUTO_ORDER:
        provider = PROVIDERS[name](settings)
        if provider.is_available():
            if name == "duckduckgo" and not settings.search_api_key:
                logger.info(
                    "SEARCH PROVIDER selected=duckduckgo (keyless; may be blocked)"
                )
            else:
                logger.info("SEARCH PROVIDER selected=%s", name)
            return provider

    raise SearchUnavailable("No web search provider is configured.")


async def probe_search(settings: Settings) -> dict:
    """Check provider availability without running a real query."""
    try:
        provider = build_search_provider(settings)
    except SearchUnavailable as exc:
        return {"available": False, "provider": None, "error": str(exc)}

    result: dict = {
        "available": provider.is_available(),
        "provider": provider.name,
        "requires_key": provider.requires_key,
    }
    if not result["available"]:
        result["error"] = "Provider requires a web search API key."

    if provider.name == "duckduckgo":
        # Keyless providers can still be blocked; report what actually happened.
        started = time.perf_counter()
        try:
            await provider.search("sauti connectivity check", max_results=1)
            result["live_probe"] = "ok"
        except SearchUnavailable as exc:
            result["live_probe"] = "blocked"
            result["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            result["live_probe"] = "error"
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["probe_ms"] = int((time.perf_counter() - started) * 1000)

    return result


def _apply_relevance(sources: list[Source], query: str) -> list[Source]:
    """Score sources by lexical overlap with the query."""
    terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
    for source in sources:
        haystack = f"{source.title} {source.snippet}".lower()
        if not terms:
            source.relevance = 0.3
        else:
            hits = sum(1 for term in terms if term in haystack)
            source.relevance = round(min(1.0, hits / len(terms)), 3)
    return sources
