"""Knowledge retrieval engine for SautiPay.

Coordinates the RAG pipeline: ingestion, chunking, embedding, search, and attribution.
"""
from __future__ import annotations

import logging
from typing import Optional

from .interfaces import (
    Chunk,
    Chunker,
    Document,
    DocumentIngestor,
    Embedder,
    RetrievalResult,
    SourceAttributor,
    VectorSearch,
)

logger = logging.getLogger(__name__)


class KnowledgeEngine:
    """Main knowledge retrieval engine.

    Coordinates the RAG pipeline components.
    """

    def __init__(
        self,
        ingestor: Optional[DocumentIngestor] = None,
        chunker: Optional[Chunker] = None,
        embedder: Optional[Embedder] = None,
        vector_search: Optional[VectorSearch] = None,
        attributor: Optional[SourceAttributor] = None,
    ):
        """Initialize the knowledge engine.

        Args:
            ingestor: Document ingestion component.
            chunker: Text chunking component.
            embedder: Text embedding component.
            vector_search: Vector search component.
            attributor: Source attribution component.
        """
        self.ingestor = ingestor
        self.chunker = chunker
        self.embedder = embedder
        self.vector_search = vector_search
        self.attributor = attributor

    def ingest(self, document: Document) -> str:
        """Ingest a document into the knowledge base.

        Args:
            document: The document to ingest.

        Returns:
            The document ID.
        """
        if self.ingestor is None:
            raise RuntimeError("DocumentIngestor not configured")
        return self.ingestor.ingest(document)

    def retrieve(
        self,
        query: str,
        language: Optional[str] = None,
        intent: Optional[str] = None,
        top_k: int = 5,
    ) -> RetrievalResult:
        """Retrieve relevant information for a query.

        Args:
            query: The user's question.
            language: Optional language code.
            intent: Optional detected intent.
            top_k: Number of results to return.

        Returns:
            RetrievalResult with matching chunks.
        """
        if self.vector_search is None or self.embedder is None:
            raise RuntimeError("VectorSearch or Embedder not configured")

        embedding = self.embedder.embed(query)
        return self.vector_search.search(
            query=query,
            embedding=embedding,
            top_k=top_k,
            language=language,
        )

    def attribute(self, chunk_ids: list[str]) -> list[dict]:
        """Get source attribution for chunk IDs.

        Args:
            chunk_ids: List of chunk IDs.

        Returns:
            A list of source information dicts.
        """
        if self.attributor is None:
            raise RuntimeError("SourceAttributor not configured")
        return self.attributor.attribute(chunk_ids)