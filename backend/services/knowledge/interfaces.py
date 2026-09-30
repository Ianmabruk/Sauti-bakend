"""Knowledge retrieval interface for SautiPay.

The RAG pipeline should:
User question
-> identify intent
-> retrieve relevant trusted information
-> provide that information to the local model
-> generate response
-> identify the source used

This module provides clean interfaces for:
document ingestion
document storage
chunking
embeddings
vector search
metadata
source attribution
retrieval
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Document:
    """A document to be ingested."""

    title: str
    content: str
    source_id: Optional[str] = None
    domain: Optional[str] = None
    language: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Chunk:
    """A chunk of a document."""

    content: str
    document_id: Optional[str] = None
    chunk_index: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class RetrievalResult:
    """Result of a retrieval operation."""

    chunks: list[Chunk]
    source_ids: list[str]
    similarity_scores: list[float]
    query: str
    language: Optional[str] = None
    intent: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "chunks": [
                {
                    "content": c.content,
                    "document_id": c.document_id,
                    "chunk_index": c.chunk_index,
                    "metadata": c.metadata,
                }
                for c in self.chunks
            ],
            "source_ids": self.source_ids,
            "similarity_scores": self.similarity_scores,
            "query": self.query,
            "language": self.language,
            "intent": self.intent,
        }


class DocumentIngestor(ABC):
    """Abstract interface for document ingestion."""

    @abstractmethod
    def ingest(self, document: Document) -> str:
        """Ingest a document and return its ID.

        Args:
            document: The document to ingest.

        Returns:
            The document ID.
        """
        ...


class Chunker(ABC):
    """Abstract interface for text chunking."""

    @abstractmethod
    def chunk(self, text: str, metadata: Optional[dict] = None) -> list[Chunk]:
        """Split text into chunks.

        Args:
            text: The text to chunk.
            metadata: Optional metadata to attach to chunks.

        Returns:
            A list of Chunk objects.
        """
        ...


class Embedder(ABC):
    """Abstract interface for text embedding."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Generate an embedding for the given text.

        Args:
            text: Input text to embed.

        Returns:
            An embedding vector.
        """
        ...


class VectorSearch(ABC):
    """Abstract interface for vector search."""

    @abstractmethod
    def search(
        self,
        query: str,
        embedding: list[float],
        top_k: int = 5,
        language: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> RetrievalResult:
        """Search for similar chunks.

        Args:
            query: The search query.
            embedding: The query embedding.
            top_k: Number of results to return.
            language: Optional language filter.
            domain: Optional domain filter.

        Returns:
            RetrievalResult with matching chunks.
        """
        ...


class SourceAttributor(ABC):
    """Abstract interface for source attribution."""

    @abstractmethod
    def attribute(self, chunk_ids: list[str]) -> list[dict]:
        """Get source information for given chunk IDs.

        Args:
            chunk_ids: List of chunk IDs to attribute.

        Returns:
            A list of source information dicts.
        """
        ...