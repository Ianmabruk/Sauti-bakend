"""Tests for the SAUTI tool layer, research pipeline and citations."""
from __future__ import annotations

import asyncio

import pytest

from backend.config.settings import Settings
from backend.services.citations import (
    Source,
    build_citations,
    classify_source,
    dedupe_sources,
    domain_of,
    extract_published_at,
)
from backend.services.research.pipeline import ResearchPipeline
from backend.services.research.providers import SearchUnavailable, build_search_provider
from backend.services.research.reader import extract_readable_text
from backend.tools.calculator import CalculatorTool, CalculatorError, safe_eval
from backend.tools.registry import ToolRegistry
from backend.tools.web_reader import WebReaderTool
from backend.tools.web_search import WebSearchTool


def run(coro):
    """Run a coroutine without needing pytest-asyncio."""
    return asyncio.run(coro)


def build_registry(provider="stub", **kwargs):
    settings = Settings(search_provider=provider, **kwargs)
    registry = ToolRegistry(settings)
    registry.register_all([
        WebSearchTool(settings),
        WebReaderTool(settings),
        CalculatorTool(settings),
    ])
    return registry, settings


HTML_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <title>Nairobi maize market reaches KSh 3,400 per bag</title>
  <meta name="description" content="Wholesale maize prices in Nairobi this week.">
  <meta property="article:published_time" content="2026-09-20T08:30:00Z">
  <link rel="canonical" href="https://www.example-markets.test/nairobi-maize">
  <link rel="stylesheet" href="https://ads.example.test/style.css">
  <script>window.tracker = {id: 99};</script>
  <style>.x{color:red}</style>
</head>
<body>
  <nav><a href="https://twitter.com/share">Tweet this</a></nav>
  <article>
    <h1>Nairobi maize market</h1>
    <p>Wholesale maize traded at <strong>KSh 3,400</strong> per 90kg bag in Nairobi
       according to traders at the main market.</p>
    <p>Eldoret reported KSh 3,250 per bag, slightly below the capital.</p>
  </article>
  <footer>Copyright</footer>
</body>
</html>
"""


class TestCalculatorSafety:
    def test_basic_arithmetic(self):
        assert safe_eval("2 + 2 * 10") == 22

    def test_parentheses(self):
        assert safe_eval("(3400 * 3) / 90") == pytest.approx(113.33, rel=1e-3)

    def test_math_functions(self):
        assert safe_eval("round(sqrt(16), 2)") == 4

    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('ls')",
            "open('/etc/passwd').read()",
            "().__class__.__bases__",
            "eval('1+1')",
            "exec('x=1')",
        ],
    )
    def test_code_execution_is_blocked(self, expression):
        with pytest.raises(CalculatorError):
            safe_eval(expression)

    def test_division_by_zero(self):
        with pytest.raises(CalculatorError):
            safe_eval("1/0")

    def test_huge_exponent_blocked(self):
        with pytest.raises(CalculatorError):
            safe_eval("9**99999")

    def test_unknown_name_blocked(self):
        with pytest.raises(CalculatorError):
            safe_eval("some_variable")


class TestToolRegistry:
    def test_registers_default_tools(self):
        registry, _ = build_registry()
        assert set(registry.names()) == {"calculator", "web_reader", "web_search"}

    def test_rejects_duplicate_tool(self):
        registry, settings = build_registry()
        with pytest.raises(ValueError):
            registry.register(CalculatorTool(settings))

    def test_describe_exposes_permission(self):
        registry, _ = build_registry()
        described = {item["name"]: item for item in registry.describe()}
        assert described["web_search"]["permission"] == "SAFE"
        assert described["calculator"]["permission"] == "COMPUTE"

    def test_unknown_tool_returns_structured_error(self):
        registry, _ = build_registry()
        result = run(registry.execute("does_not_exist", {}))
        assert result.ok is False
        assert "Unknown tool" in result.error

    def test_missing_required_argument(self):
        registry, _ = build_registry()
        result = run(registry.execute("calculator", {}))
        assert result.ok is False
        assert "expression" in result.error

    def test_wrong_argument_type(self):
        registry, _ = build_registry()
        result = run(registry.execute("calculator", {"expression": 12345}))
        assert result.ok is False

    def test_unknown_extra_argument_rejected(self):
        registry, _ = build_registry()
        result = run(registry.execute("calculator", {"expression": "1+1", "evil": True}))
        assert result.ok is False

    def test_too_long_argument_rejected(self):
        registry, _ = build_registry()
        result = run(registry.execute("web_search", {"query": "x" * 500}))
        assert result.ok is False


class TestToolExecution:
    def test_calculator_executes(self):
        registry, _ = build_registry()
        result = run(registry.execute("calculator", {"expression": "1250 * 2"}))
        assert result.ok is True
        assert result.data["result"] == 2500

    def test_web_search_returns_sources(self):
        registry, _ = build_registry()
        result = run(registry.execute("web_search", {"query": "current maize price Kenya"}))
        assert result.ok is True
        assert len(result.sources) > 0

    def test_web_reader_rejects_non_http(self):
        registry, _ = build_registry()
        result = run(registry.execute("web_reader", {"url": "file:///etc/passwd"}))
        assert result.ok is False
        assert "non-http" in result.error

    def test_web_reader_rejects_missing_url(self):
        registry, _ = build_registry()
        result = run(registry.execute("web_reader", {}))
        assert result.ok is False


class TestPermissionModel:
    def test_dangerous_tool_is_denied_by_default(self):
        from backend.tools.base import PermissionLevel, Tool, ToolResult

        class DangerTool(Tool):
            name = "rm_rf"
            description = "dangerous"
            input_schema = {"type": "object", "properties": {}}
            permission = PermissionLevel.PROCESS

            async def run(self, arguments):
                return ToolResult(tool=self.name, ok=True)

        settings = Settings(enable_dangerous_tools=False)
        registry = ToolRegistry(settings)
        registry.register(DangerTool())
        result = run(registry.execute("rm_rf", {}))
        assert result.ok is False
        assert "PROCESS" in result.error

    def test_safe_tool_is_allowed(self):
        registry, _ = build_registry()
        result = run(registry.execute("calculator", {"expression": "2+2"}))
        assert result.ok is True


class TestHtmlExtraction:
    def test_extracts_title_and_body(self):
        text, meta = extract_readable_text(HTML_PAGE)
        assert meta["title"] == "Nairobi maize market reaches KSh 3,400 per bag"
        assert "Nairobi maize market" in text
        assert "3,400" in text

    def test_drops_script_and_style(self):
        text, _ = extract_readable_text(HTML_PAGE)
        assert "window.tracker" not in text
        assert "color:red" not in text

    def test_drops_social_share_links(self):
        text, _ = extract_readable_text(HTML_PAGE)
        assert "Tweet this" not in text

    def test_extracts_canonical_url(self):
        _, meta = extract_readable_text(HTML_PAGE)
        assert meta["canonical"] == "https://www.example-markets.test/nairobi-maize"

    def test_survives_malformed_html(self):
        text, _ = extract_readable_text("<html><p>unclosed<div><span>text")
        assert "text" in text


class TestPublicationDates:
    def test_reads_article_published_time(self):
        assert extract_published_at(HTML_PAGE) == "2026-09-20"

    def test_returns_none_when_absent(self):
        assert extract_published_at("<html><body><p>no date here</p></body></html>") is None

    def test_never_guesses(self):
        # A bare year is not a trustworthy publication date.
        assert extract_published_at("<p>Copyright 2026 Example Ltd</p>") is None


class TestSourceClassification:
    def test_official_domain(self):
        assert classify_source("https://www.kilimo.go.ke/bulletin", "Bulletin") == "official"

    def test_news_domain(self):
        assert classify_source("https://nation.africa/kenya", "News") == "news"

    def test_low_quality_domain(self):
        assert classify_source("https://www.pinterest.com/pin/1", "Pin") == "low_quality"

    def test_domain_of_strips_www(self):
        assert domain_of("https://www.example.com/a/b") == "example.com"

    def test_domain_of_invalid(self):
        assert domain_of("not a url") == ""


class TestCitations:
    def test_includes_verified_urls_only(self):
        sources = [
            Source(title="A", url="https://a.test/x", relevance=0.9),
            Source(title="No URL", url="", relevance=0.9),
        ]
        citations = build_citations(sources)
        assert len(citations) == 1
        assert citations[0]["url"] == "https://a.test/x"

    def test_missing_date_is_none(self):
        citations = build_citations([Source(title="A", url="https://a.test/x")])
        assert citations[0]["date"] is None

    def test_ranks_official_sources_first(self):
        sources = [
            Source(title="Blog", url="https://random.test/p", relevance=0.5),
            Source(title="Gov", url="https://www.kilimo.go.ke/b", relevance=0.5),
        ]
        citations = build_citations(sources)
        assert citations[0]["domain"] == "kilimo.go.ke"

    def test_respects_limit(self):
        sources = [Source(title=f"S{i}", url=f"https://s{i}.test/x", relevance=i / 10) for i in range(10)]
        assert len(build_citations(sources, limit=3)) == 3

    def test_dedupe_by_url(self):
        sources = [
            Source(title="A", url="https://a.test/x", relevance=0.2),
            Source(title="A better", url="https://a.test/x", relevance=0.9),
        ]
        deduped = dedupe_sources(sources)
        assert len(deduped) == 1
        assert deduped[0].title == "A better"


class TestResearchPipeline:
    def test_successful_run_returns_sources(self):
        settings = Settings(search_provider="stub")
        result = run(ResearchPipeline(settings).run("current maize price Kenya"))
        assert result.ok is True
        assert len(result.sources) > 0
        assert result.provider == "stub"

    def test_weather_queries_use_open_meteo_without_key(self):
        settings = Settings(search_provider="auto", search_api_key="", user_city="Nairobi", user_country="Kenya")
        result = run(ResearchPipeline(settings).run("what is the weather forecast in Nairobi"))
        assert result.ok is True
        assert result.provider == "open-meteo"
        assert result.sources

    def test_no_results_is_reported_not_faked(self):
        settings = Settings(search_provider="failing")
        result = run(ResearchPipeline(settings).run("current maize price Kenya"))
        assert result.ok is False
        assert result.error
        assert result.sources == []

    def test_missing_provider_is_reported(self):
        settings = Settings(search_provider="nonexistent")
        result = run(ResearchPipeline(settings).run("anything"))
        assert result.ok is False
        assert "Unknown SEARCH_PROVIDER" in result.error

    def test_activity_records_stages(self):
        settings = Settings(search_provider="stub")
        result = run(ResearchPipeline(settings).run("maize price Kenya"))
        stages = [entry["stage"] for entry in result.activity]
        assert "searching" in stages
        assert "search_complete" in stages

    def test_read_pages_can_be_disabled(self):
        settings = Settings(search_provider="stub")
        result = run(ResearchPipeline(settings).run("maize price Kenya", read_pages=False))
        assert result.ok is True
        assert result.read_count == 0

    def test_read_failures_do_not_crash_pipeline(self):
        # Stub URLs do not resolve, so reading must fail gracefully.
        settings = Settings(search_provider="stub")
        result = run(ResearchPipeline(settings).run("maize price Kenya", read_pages=True))
        assert result.ok is True
        assert result.sources


class TestProviderSelection:
    def test_auto_prefers_keyed_provider(self):
        settings = Settings(search_provider="auto", search_api_key="k")
        assert build_search_provider(settings).name == "brave"

    def test_auto_falls_back_to_keyless(self):
        settings = Settings(search_provider="auto", search_api_key="")
        assert build_search_provider(settings).name == "duckduckgo"

    def test_keyed_provider_requires_key(self):
        settings = Settings(search_provider="brave", search_api_key="")
        with pytest.raises(SearchUnavailable):
            build_search_provider(settings)

    def test_stub_always_available(self):
        settings = Settings(search_provider="stub")
        assert build_search_provider(settings).name == "stub"
