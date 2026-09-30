"""Research services: pluggable search, page reading, and the pipeline."""
from .pipeline import (  # noqa: F401
    ResearchPipeline,
    ResearchResult,
    build_search_query,
    needs_research,
    run_research,
)
from .providers import (  # noqa: F401
    FailingSearchProvider,
    SearchProvider,
    SearchUnavailable,
    StubSearchProvider,
    build_search_provider,
    probe_search,
)
from .reader import ReadError, WebReader, extract_readable_text  # noqa: F401

__all__ = [
    "ResearchPipeline",
    "ResearchResult",
    "build_search_query",
    "needs_research",
    "run_research",
    "SearchProvider",
    "SearchUnavailable",
    "StubSearchProvider",
    "FailingSearchProvider",
    "build_search_provider",
    "probe_search",
    "WebReader",
    "ReadError",
    "extract_readable_text",
]
