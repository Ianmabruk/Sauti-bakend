"""Web page reading and main-content extraction.

Uses the standard library's ``html.parser`` so the project does not need an
HTML-extraction dependency. The goal is a readable text body plus metadata
(title, publication date when the page states one).
"""
from __future__ import annotations

import asyncio
import logging
import re
from html.parser import HTMLParser
from typing import Optional

import httpx

from ...config.settings import Settings
from ..citations import Source, classify_source, domain_of, extract_published_at

logger = logging.getLogger(__name__)

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "iframe"}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "li", "tr", "br", "h1", "h2", "h3",
    "h4", "h5", "h6", "blockquote", "pre", "ul", "ol", "table", "header",
    "footer", "main", "figure",
}
_DROP_LINK_HOSTS = (
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "pinterest.", "t.me", "whatsapp.com",
)


class ReadError(RuntimeError):
    """A page could not be retrieved or parsed."""


class _Extractor(HTMLParser):
    """Collect visible text plus useful metadata from an HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self._drop_link_depth = 0
        self.title: Optional[str] = None
        self.meta_description: Optional[str] = None
        self.canonical: Optional[str] = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attr = {k.lower(): (v or "") for k, v in attrs}

        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (attr.get("name") or attr.get("property") or "").lower()
            if name in {"description", "og:description"} and not self.meta_description:
                self.meta_description = attr.get("content", "").strip()
            elif name in {"og:title", "twitter:title"} and not self.title:
                self.title = attr.get("content", "").strip()
        elif tag == "link" and attr.get("rel", "").lower() == "canonical":
            self.canonical = attr.get("href", "").strip() or None
        elif tag == "a" and self._is_low_value_link(attr.get("href", "")):
            self._drop_link_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "a":
            self._drop_link_depth = max(0, self._drop_link_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def _is_low_value_link(self, href: str) -> bool:
        return any(host in href.lower() for host in _DROP_LINK_HOSTS)

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            stripped = data.strip()
            if stripped:
                self.title = stripped
        if self._skip_depth or self._drop_link_depth:
            return
        if data.strip():
            self._parts.append(data)

    @property
    def text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*", "\n\n", raw)
        return raw.strip()


def extract_readable_text(html: str) -> tuple[str, dict]:
    """Extract readable text and metadata from an HTML string.

    Args:
        html: Raw HTML.

    Returns:
        (text, metadata) where metadata holds title/description/canonical.
    """
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - malformed HTML must not crash us
        logger.debug("HTML parse warning: %s", exc)
    meta = {
        "title": parser.title,
        "description": parser.meta_description,
        "canonical": parser.canonical,
    }
    return parser.text, meta


def _is_html(content_type: str) -> bool:
    return "html" in content_type.lower() or "xml" in content_type.lower()


class WebReader:
    """Fetches pages and turns them into Sources."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def read(self, url: str, query: str = "") -> Source:
        """Fetch and extract a single page.

        Args:
            url: Absolute http(s) URL.
            query: Original query, used for relevance scoring.

        Returns:
            A Source with populated content.

        Raises:
            ReadError: On invalid URL, network failure, or unusable content.
        """
        if not url or not url.startswith(("http://", "https://")):
            raise ReadError(f"Refusing to read non-http(s) URL: {url!r}")

        headers = {
            "User-Agent": self.settings.reader_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,sw;q=0.8,fr;q=0.8",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.reader_timeout,
                follow_redirects=True,
                max_redirects=4,
            ) as client:
                response = await self._get_with_retry(client, url, headers)
        except httpx.TimeoutException as exc:
            raise ReadError(f"Timed out reading {url}") from exc
        except httpx.HTTPError as exc:
            raise ReadError(f"Network error reading {url}: {exc}") from exc

        if response.status_code >= 400:
            raise ReadError(f"{url} returned HTTP {response.status_code}")

        content_type = response.headers.get("content-type", "")
        body = response.text
        if len(body) > self.settings.reader_max_bytes:
            body = body[: self.settings.reader_max_bytes]

        final_url = str(response.url)
        title = ""
        snippet = ""
        content = ""

        if _is_html(content_type) or "<html" in body[:2000].lower():
            text, meta = extract_readable_text(body)
            title = meta.get("title") or ""
            snippet = meta.get("description") or ""
            published = extract_published_at(body)
            content = text
        else:
            content = body.strip()
            published = None

        if not content:
            raise ReadError(f"No readable content extracted from {url}")

        final_url = meta.get("canonical") if _is_html(content_type) and meta.get("canonical") else final_url
        if not title:
            title = f"{domain_of(final_url)} page"

        source = Source(
            title=title.strip()[:300],
            url=final_url,
            domain=domain_of(final_url),
            snippet=(snippet or content[:280]).strip(),
            published_at=published,
            source_type=classify_source(final_url, title),
            content=content,
        )
        source.relevance = _score(source, query)
        return source

    async def _get_with_retry(self, client: httpx.AsyncClient, url: str, headers: dict):
        last: Optional[Exception] = None
        for attempt in range(2):
            try:
                return await client.get(url, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                if attempt == 0:
                    await asyncio.sleep(0.3)
        raise last if last else ReadError("read failed")

    async def read_many(self, urls: list[str], query: str = "") -> tuple[list[Source], list[str]]:
        """Read several pages concurrently.

        Independent fetches run in parallel rather than sequentially.

        Returns:
            (sources, errors) where errors are "url: reason" strings.
        """
        if not urls:
            return [], []

        tasks = [self.read(url, query=query) for url in urls]
        settled = await asyncio.gather(*tasks, return_exceptions=True)

        sources: list[Source] = []
        errors: list[str] = []
        for url, outcome in zip(urls, settled):
            if isinstance(outcome, Source):
                sources.append(outcome)
            elif isinstance(outcome, ReadError):
                errors.append(f"{url}: {outcome}")
            else:
                errors.append(f"{url}: {type(outcome).__name__}: {outcome}")
        return sources, errors


def _score(source: Source, query: str) -> float:
    if not query:
        return 0.4
    terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
    if not terms:
        return 0.4
    haystack = f"{source.title} {source.snippet} {source.content[:4000]}".lower()
    return round(min(1.0, sum(1 for t in terms if t in haystack) / len(terms)), 3)
