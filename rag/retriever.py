"""Retriever for RAG knowledge base.

Retrieves relevant documents based on query, call stage, and applies
boosting for compliance and script documents.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from rag.document import Document
from rag.embeddings import EmbeddingProvider, get_embedding_provider
from rag.vector_store import BaseVectorStore, get_vector_store

logger = logging.getLogger(__name__)


class Retriever:
    """Retrieves and ranks documents from the vector store using hybrid retrieval.

    Args:
        embedding_provider: EmbeddingProvider instance for query encoding.
        vector_store: Vector store backend. Uses default if not provided.
    """

    def __init__(
        self,
        embedding_provider: Optional[EmbeddingProvider] = None,
        vector_store: Optional[BaseVectorStore] = None,
    ) -> None:
        # Load settings from environment variables with safe fallbacks
        self._provider_name = os.environ.get("DANA_EMBEDDING_PROVIDER")
        self._embedder = embedding_provider or get_embedding_provider(self._provider_name)
        self._store = vector_store or get_vector_store()
        self._default_top_k = int(os.environ.get("DANA_RAG_TOP_K", 5))
        self._approved_only = os.environ.get("DANA_RAG_APPROVED_ONLY", "true").lower() == "true"
        self._enable_hybrid = os.environ.get("DANA_RAG_ENABLE_HYBRID", "true").lower() == "true"

    def retrieve(
        self,
        query: str,
        call_stage: str = "",
        top_k: Optional[int] = None,
        approved_only: Optional[bool] = None,
        filters: Optional[dict] = None,
        topic: Optional[str] = None,
        doc_type: Optional[str] = None,
    ) -> list[Document]:
        """Retrieve relevant documents for a query.

        Args:
            query: The search query text.
            call_stage: Current call stage for relevance filtering and boosting.
            top_k: Maximum number of documents to return.
            approved_only: If True, filters out unapproved documents.
            filters: Optional dict of hard metadata filters.
            topic: Optional topic filter/boost.
            doc_type: Optional document type filter/boost.

        Returns:
            List of Documents ordered by relevance score (highest first).
        """
        if not query or not query.strip():
            return []

        limit_k = top_k if top_k is not None else self._default_top_k
        app_only = approved_only if approved_only is not None else self._approved_only

        # Generate query embedding
        query_embedding = self._embedder.embed(query)

        # Call search on vector store
        # Vector store handles approved_only, filters, call_stage, topic, and doc_type
        results = self._store.search(
            query_embedding=query_embedding,
            query_text=query,
            top_k=limit_k,
            approved_only=app_only,
            filters=filters,
            call_stage=call_stage,
            topic=topic,
            doc_type=doc_type,
        )

        if not results:
            return []

        # Return Documents extracted from search results
        # SearchResults are already sorted by final_score descending
        return [res.document for res in results]
