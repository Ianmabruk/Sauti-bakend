"""Web search: keyless Parallel Search over MCP, DuckDuckGo as backup.

Parallel's hosted MCP server at ``https://search.parallel.ai/mcp`` needs no
account and no API key, so it is the default. It speaks MCP over Streamable
HTTP, which means a JSON-RPC handshake, a session header, and two tools:
``web_search`` and ``web_fetch``.

Everything is cached for ten minutes by default, because voice users repeat
themselves ("and the price?") and a re-fetch both wastes the free quota and
adds seconds of latency.

If the MCP server is unreachable or rate-limited, the provider degrades to
scraping DuckDuckGo's HTML endpoint, which needs no key either. Only when both
paths fail does the agent tell the user it could not verify anything online.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from ..cache import TTLCache
from ..config import Settings
from ..logsetup import get_logger
from ..net import HttpPool, get_pool, iter_sse
from ..retry import ProviderError, RetryPolicy, with_retry

logger = get_logger(__name__)

#: MCP protocol revision we negotiate.
PROTOCOL_VERSION = "2025-06-18"

_DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
#: DuckDuckGo's HTML endpoint yields redirect links we must unwrap.
_DDG_REDIRECT = re.compile(r"//duckduckgo\.com/l/\?uddg=([^&\"]+)")


def _extract_excerpt(entry: dict[str, Any]) -> str:
    """Pull readable text out of a search result, whatever shape it arrived in.

    Parallel returns ``excerpts`` as a list of strings; other backends use a
    single ``excerpt``/``content`` string. All of them are accepted so the
    prompt assembly never has to care which provider ran.

    Args:
        entry: One raw result object from a provider.

    Returns:
        Whitespace-collapsed excerpt text, capped for prompt budget.
    """
    raw: Any = entry.get("excerpts")
    if isinstance(raw, list):
        pieces = [str(item) for item in raw if item]
        text = " ".join(pieces)
    elif raw:
        text = str(raw)
    else:
        text = str(
            entry.get("excerpt")
            or entry.get("content")
            or entry.get("text")
            or entry.get("description")
            or entry.get("summary")
            or ""
        )
    return " ".join(text.split())[:1200]


@dataclass
class SearchResult:
    """Outcome of one search.

    Attributes:
        ok: False when nothing usable was retrieved.
        query: The query actually issued (after rewriting).
        sources: Ranked sources with title, url, excerpt and date.
        provider: Which backend produced the results.
        duration_ms: Wall time.
        cached: True when served from the cache.
        reason: Explanation when ``ok`` is False.
    """

    ok: bool = True
    query: str = ""
    sources: list[dict[str, str]] = field(default_factory=list)
    provider: str = ""
    duration_ms: int = 0
    cached: bool = False
    reason: str = ""

    def top(self, limit: int = 2) -> list[dict[str, str]]:
        """The highest ranked sources, for citation."""
        return self.sources[:limit]

    def summary(self, max_chars: int = 3500) -> str:
        """A compact digest for the model's prompt."""
        if not self.sources:
            return ""
        lines: list[str] = []
        for source in self.sources:
            excerpt = " ".join((source.get("excerpt") or "").split())
            if not excerpt:
                continue
            lines.append(f"{source.get('title', 'untitled')}: {excerpt[:400]}")
            if sum(len(line) for line in lines) > max_chars:
                break
        return "\n".join(lines)


class SearchProvider:
    """Base class for search backends."""

    name: str = "base"

    @property
    def available(self) -> bool:
        """True when this backend could answer."""
        return False

    async def search(
        self, query: str, *, objective: str = "", limit: int = 6
    ) -> SearchResult:
        """Run a search.

        Args:
            query: Rewritten keyword query.
            objective: Natural-language description of what is needed.
            limit: Maximum sources to return.

        Returns:
            A :class:`SearchResult`.
        """
        raise NotImplementedError

    async def fetch(self, urls: list[str], *, objective: str = "") -> SearchResult:
        """Fetch and extract page text.

        Args:
            urls: Absolute URLs.
            objective: What to look for.

        Returns:
            A :class:`SearchResult` whose sources carry the page excerpts.
        """
        return SearchResult(ok=False, provider=self.name, reason="fetch not supported")


class ParallelMcpProvider(SearchProvider):
    """Keyless search through Parallel's hosted MCP server.

    The MCP session is established lazily and cached in memory, because
    re-running ``initialize`` on every search would double the latency.

    Args:
        settings: Supplies the endpoint, session id and timeouts.
        pool: Shared HTTP pool.
    """

    name = "parallel-mcp"

    def __init__(self, settings: Optional[Settings] = None, *, pool: Optional[HttpPool] = None) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self.pool = pool
        self._session_id: Optional[str] = None
        self._request_id = 0
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        """True when search is enabled in configuration."""
        return self.settings.search_enabled

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "JARVIS/1.0",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _client(self) -> HttpPool:
        return self.pool or get_pool(self.settings)

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        """Issue one JSON-RPC request, handling both JSON and SSE replies."""
        body = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            body["params"] = params

        pool = self._client()
        async with pool.client.stream(
            "POST",
            self.settings.search_url,
            json=body,
            headers=self._headers(),
            timeout=httpx.Timeout(self.settings.search_timeout, connect=5.0),
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                response.raise_for_status()
            session = response.headers.get("mcp-session-id")
            if session:
                self._session_id = session
            async for event in iter_sse(response):
                if isinstance(event, dict):
                    return event
        raise ProviderError("MCP server returned no JSON-RPC response")

    async def _ensure_session(self) -> None:
        """Run the MCP handshake once, guarded by a lock."""
        if self._session_id is not None:
            return
        async with self._lock:
            if self._session_id is not None:
                return
            logger.info("establishing MCP session with %s", self.settings.search_url)
            result = await self._rpc(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "jarvis", "version": "1.0"},
                },
            )
            if "error" in result:
                raise ProviderError(f"MCP init failed: {result['error']}")
            # The spec requires this notification before any other call.
            try:
                await self._notify_initialised()
            except Exception as exc:  # noqa: BLE001
                logger.debug("initialised notification failed (continuing): %s", exc)
            logger.info("MCP session established id=%s", (self._session_id or "n/a")[:8])

    async def _notify_initialised(self) -> None:
        """Send the ``notifications/initialized`` ping."""
        pool = self._client()
        response = await pool.client.post(
            self.settings.search_url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=self._headers(),
            timeout=httpx.Timeout(self.settings.search_timeout, connect=5.0),
        )
        if response.status_code >= 400:
            logger.debug("initialized notification returned %d", response.status_code)

    async def _call_tool(self, tool: str, arguments: dict) -> dict:
        """Call an MCP tool and return its text content joined together."""
        await self._ensure_session()
        result = await self._rpc(
            "tools/call", {"name": tool, "arguments": arguments}
        )
        if "error" in result:
            raise ProviderError(f"MCP tool '{tool}' failed: {result['error']}")

        payload = result.get("result", {}) or {}
        if payload.get("isError"):
            raise ProviderError(f"MCP tool '{tool}' reported an error")

        pieces: list[str] = []
        for block in payload.get("content", []) or []:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    pieces.append(text)
        return {"text": "\n".join(pieces)}

    @staticmethod
    def _parse_results(raw: str) -> list[dict[str, str]]:
        """Normalise a Parallel payload into our source shape.

        The server may answer with JSON or with a text summary plus JSON, so we
        try several shapes and give up quietly rather than raising.
        """
        text = (raw or "").strip()
        if not text:
            return []

        candidates: list[Any] = []
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                candidates.append(json.loads(text[start : end + 1]))
            except json.JSONDecodeError:
                pass
        try:
            candidates.append(json.loads(text))
        except json.JSONDecodeError:
            pass

        results: list[dict[str, str]] = []
        for payload in candidates:
            if not isinstance(payload, dict):
                continue
            entries = (
                payload.get("results")
                or payload.get("search_results")
                or payload.get("data")
                or []
            )
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                url = str(entry.get("url") or entry.get("link") or "").strip()
                if not url:
                    continue
                results.append(
                    {
                        "title": str(entry.get("title") or url).strip(),
                        "url": url,
                        "excerpt": _extract_excerpt(entry),
                        "date": str(
                            entry.get("publish_date")
                            or entry.get("published_date")
                            or entry.get("date")
                            or ""
                        ),
                    }
                )
            if results:
                break
        return results

    @property
    def policy(self) -> RetryPolicy:
        """Retry policy for MCP calls.

        The free endpoint returns intermittent 502s, so a couple of retries
        with backoff usually succeed before we fall through to DuckDuckGo.
        """
        return RetryPolicy(
            attempts=self.settings.search_max_retries + 1,
            base=self.settings.llm_backoff_base,
            maximum=self.settings.llm_backoff_max,
        )

    async def _call_tool_resilient(self, tool: str, arguments: dict) -> dict:
        """Call an MCP tool with retries, resetting the session on failure.

        A dead session id produces the same error as a flaky server, so the
        session is cleared whenever a call fails, forcing a fresh handshake on
        the next attempt.
        """

        async def _attempt() -> dict:
            try:
                return await self._call_tool(tool, arguments)
            except Exception:
                self._session_id = None
                raise

        return await with_retry(
            _attempt,
            policy=self.policy,
            label=f"parallel.{tool}",
            timeout=self.settings.search_timeout * (self.settings.search_max_retries + 1),
        )

    async def search(
        self, query: str, *, objective: str = "", limit: int = 6
    ) -> SearchResult:
        """Search the web through the keyless MCP server.

        Args:
            query: Rewritten keyword query.
            objective: Natural-language description of the need.
            limit: Maximum sources.

        Returns:
            A :class:`SearchResult`. Never raises.
        """
        started = time.perf_counter()
        queries = [part for part in re.split(r"\s*\|\s*", query) if part][:3]
        if not queries:
            return SearchResult(ok=False, provider=self.name, reason="empty query")

        arguments = {
            "objective": objective or query,
            "search_queries": queries or [query],
            "session_id": self.settings.search_session,
        }

        try:
            raw = await self._call_tool_resilient("web_search", arguments)
        except (ProviderError, httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.warning("parallel search failed: %s", exc)
            return SearchResult(
                ok=False,
                query=query,
                provider=self.name,
                reason=str(exc),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        sources = self._parse_results(raw.get("text", ""))[:limit]
        duration = int((time.perf_counter() - started) * 1000)
        logger.info(
            "search done provider=%s query=%r sources=%d duration_ms=%d",
            self.name, query, len(sources), duration,
        )
        return SearchResult(
            ok=bool(sources),
            query=query,
            sources=sources,
            provider=self.name,
            duration_ms=duration,
            reason="" if sources else "The search returned no usable results.",
        )

    async def fetch(self, urls: list[str], *, objective: str = "") -> SearchResult:
        """Extract page text through the MCP ``web_fetch`` tool."""
        started = time.perf_counter()
        if not urls:
            return SearchResult(ok=False, provider=self.name, reason="no urls")
        try:
            raw = await self._call_tool_resilient(
                "web_fetch",
                {
                    "urls": urls[:10],
                    "objective": objective[:200] or None,
                    "session_id": self.settings.search_session,
                },
            )
        except (ProviderError, httpx.HTTPError, asyncio.TimeoutError) as exc:
            return SearchResult(ok=False, provider=self.name, reason=str(exc))

        sources = self._parse_results(raw.get("text", ""))
        if not sources:
            body = " ".join(raw.get("text", "").split())
            sources = [
                {
                    "title": url,
                    "url": url,
                    "excerpt": body[:4000],
                    "date": "",
                }
                for url in urls[:5]
            ]
        return SearchResult(
            ok=bool(sources),
            sources=sources,
            provider=self.name,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class DuckDuckGoProvider(SearchProvider):
    """Keyless DuckDuckGo HTML scrape, used when MCP is unavailable.

    Deliberately simple: it parses the no-JS results page, which needs no
    credentials and no SDK. Slower and less tidy than MCP, but it keeps the
    agent honest about current information when the primary path is down.
    """

    name = "duckduckgo"

    _ENTRY = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S
    )
    _SNIPPET = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S)
    _TAG = re.compile(r"<[^>]+>")

    def __init__(self, settings: Optional[Settings] = None, *, pool: Optional[HttpPool] = None) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self.pool = pool

    @property
    def available(self) -> bool:
        """Always available; it needs no configuration."""
        return True

    @classmethod
    def _clean(cls, fragment: str) -> str:
        """Strip tags and unescape entities from an HTML fragment."""
        import html as html_mod

        return " ".join(html_mod.unescape(cls._TAG.sub("", fragment)).split())

    @classmethod
    def _unwrap(cls, href: str) -> str:
        """Decode a DuckDuckGo redirect to the real destination URL."""
        from urllib.parse import parse_qs, unquote, urlparse

        match = _DDG_REDIRECT.search(href)
        if match:
            return unquote(parse_qs(urlparse(href if href.startswith("http") else f"https:{href}").query).get("uddg", [""])[0])
        if href.startswith("//duckduckgo.com/l/?"):
            return unquote(parse_qs(urlparse(f"https:{href}").query).get("uddg", [""])[0])
        return href

    async def search(
        self, query: str, *, objective: str = "", limit: int = 6
    ) -> SearchResult:
        """Scrape DuckDuckGo for results.

        Args:
            query: Rewritten keyword query.
            objective: Unused; accepted for interface parity.
            limit: Maximum sources.

        Returns:
            A :class:`SearchResult`. Never raises.
        """
        started = time.perf_counter()
        pool = self.pool or get_pool(self.settings)
        try:
            response = await pool.client.post(
                _DDG_ENDPOINT,
                data={"q": query, "kl": "wt-wt"},
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) JARVIS/1.0"},
                timeout=httpx.Timeout(self.settings.search_timeout, connect=5.0),
            )
            response.raise_for_status()
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.warning("duckduckgo failed: %s", exc)
            return SearchResult(
                ok=False, query=query, provider=self.name, reason=str(exc),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        html = response.text
        snippets = [self._clean(s) for s in self._SNIPPET.findall(html)]
        sources: list[dict[str, str]] = []
        for index, (href, title_html) in enumerate(self._ENTRY.findall(html)):
            url = self._unwrap(href)
            if not url.startswith("http"):
                continue
            sources.append(
                {
                    "title": self._clean(title_html) or url,
                    "url": url,
                    "excerpt": snippets[index] if index < len(snippets) else "",
                    "date": "",
                }
            )
            if len(sources) >= limit:
                break

        duration = int((time.perf_counter() - started) * 1000)
        logger.info(
            "search done provider=%s query=%r sources=%d duration_ms=%d",
            self.name, query, len(sources), duration,
        )
        return SearchResult(
            ok=bool(sources),
            query=query,
            sources=sources,
            provider=self.name,
            duration_ms=duration,
            reason="" if sources else "No results were found.",
        )


class CachedSearchProvider(SearchProvider):
    """Wraps another provider with a TTL cache and a fallback chain.

    Args:
        primary: Tried first; usually the MCP provider.
        secondary: Tried when the primary fails; usually DuckDuckGo.
        cache: Shared cache instance, also used for fetch results.
    """

    def __init__(
        self,
        primary: SearchProvider,
        secondary: SearchProvider,
        cache: TTLCache,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.cache = cache
        self.name = f"cached[{primary.name}->{secondary.name}]"

    @property
    def available(self) -> bool:
        """True when either backend is available."""
        return self.primary.available or self.secondary.available

    async def search(
        self, query: str, *, objective: str = "", limit: int = 6
    ) -> SearchResult:
        """Search with caching and automatic backend failover."""
        key = f"s:{query.strip().lower()}:{limit}"
        cached = self.cache.get(key)
        if cached is not None:
            logger.info("search cache hit query=%r", query)
            return SearchResult(
                ok=cached.ok,
                query=cached.query,
                sources=cached.sources,
                provider=cached.provider,
                duration_ms=0,
                cached=True,
                reason=cached.reason,
            )

        result = await self.primary.search(query, objective=objective, limit=limit)
        if not result.ok and self.secondary is not self.primary:
            logger.info(
                "primary search failed (%s), trying %s", result.reason, self.secondary.name
            )
            result = await self.secondary.search(query, objective=objective, limit=limit)
            result.provider = result.provider or self.secondary.name

        if result.ok:
            self.cache.set(key, result)
        else:
            # Cache the failure briefly-equivalently: not at all, so a
            # transient outage is retried on the very next question.
            logger.info("search failed query=%r reason=%s", query, result.reason)
        return result

    async def fetch(self, urls: list[str], *, objective: str = "") -> SearchResult:
        """Fetch pages, memoised per URL set."""
        key = "f:" + "|".join(sorted(urls))
        cached = self.cache.get(key)
        if cached is not None:
            return SearchResult(
                ok=cached.ok,
                sources=cached.sources,
                provider=cached.provider,
                cached=True,
                reason=cached.reason,
            )
        result = await self.primary.fetch(urls, objective=objective)
        if not result.ok and self.secondary is not self.primary:
            result = await self.secondary.fetch(urls, objective=objective)
        if result.ok:
            self.cache.set(key, result)
        return result


def build_search_provider(
    settings: Optional[Settings] = None, *, pool: Optional[HttpPool] = None
) -> CachedSearchProvider:
    """Construct the default search stack: MCP, then DuckDuckGo, cached.

    Args:
        settings: Configuration.
        pool: Optional shared HTTP pool.

    Returns:
        A ready-to-use provider.
    """
    from ..config import get_settings

    cfg = settings or get_settings()
    cache = TTLCache(
        max_size=cfg.search_cache_max, ttl=cfg.search_cache_ttl
    )
    return CachedSearchProvider(
        primary=ParallelMcpProvider(cfg, pool=pool),
        secondary=DuckDuckGoProvider(cfg, pool=pool),
        cache=cache,
    )
