"""Research API: direct access to the web research pipeline.

The agent uses this service in-process; this route exists so the pipeline can be
inspected, debugged and used directly.
"""
from __future__ import annotations

import asyncio
import logging

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from ..agent.language import SautiLanguageDetector
from ..config.settings import Settings
from ..schemas.research import ResearchRequest
from ..services.research.pipeline import ResearchPipeline
from ..services.research.providers import probe_search

logger = logging.getLogger(__name__)

research_bp = Blueprint("research", __name__)
detector = SautiLanguageDetector()


@research_bp.route("/research", methods=["POST"])
def research():
    """Run the research pipeline for a query.

    Body: {"query": "current maize prices Kenya", "language": "auto",
           "read_pages": true, "max_results": 8}
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be valid JSON"}), 400

    try:
        parsed = ResearchRequest(**data)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", ())) or "body"
        return jsonify({"error": f"{field}: {first.get('msg')}"}), 400

    settings = Settings()
    language = parsed.language
    if language == "auto":
        language = detector.detect(parsed.query)

    result = asyncio.run(
        ResearchPipeline(settings).run(
            parsed.query,
            language=language,
            read_pages=parsed.read_pages,
            max_results=parsed.max_results,
        )
    )

    body = result.to_dict()
    body["language"] = language
    # A pipeline that ran but found nothing is still a 200; a pipeline that
    # could not run at all is a 503.
    return jsonify(body), (200 if result.ok else 503)


@research_bp.route("/research/status", methods=["GET"])
def research_status():
    """Report whether web research is currently possible, and why not."""
    settings = Settings()
    probe = asyncio.run(probe_search(settings))
    probe["llm_configured"] = settings.llm_configured
    probe["model_provider"] = settings.model_provider
    return jsonify(probe)
