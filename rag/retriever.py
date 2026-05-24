"""Retriever for RAG knowledge base.

Retrieves relevant documents based on query, call stage, and applies
boosting for compliance and script documents.
"""

from __future__ import annotations

import logging
from typing import Optional

from rag.document import Document
from rag.embeddings import EmbeddingProvider
from rag.vector_store import BaseVectorStore, get_vector_store

logger = logging.getLogger(__name__)

# Boost factors for different document types
_COMPLIANCE_BOOST = 1.5
_SCRIPT_BOOST = 1.3
_STAGE_MATCH_BOOST = 1.4


class Retriever:
    """Retrieves and ranks documents from the vector store.

    Applies relevance filtering by call stage and boosts compliance
    and script documents to ensure they appear at the top.

    Args:
        embedding_provider: EmbeddingProvider instance for query encoding.
        vector_store: Vector store backend. Uses default if not provided.
    """

    def __init__(
        self,
        embedding_provider: Optional[EmbeddingProvider] = None,
        vector_store: Optional[BaseVectorStore] = None,
    ) -> None:
        self._embedder = embedding_provider or EmbeddingProvider()
        self._store = vector_store or get_vector_store()

    def retrieve(
        self,
        query: str,
        call_stage: str = "",
        top_k: int = 5,
    ) -> list[Document]:
        """Retrieve relevant documents for a query.

        Args:
            query: The search query text.
            call_stage: Current call stage for relevance filtering.
            top_k: Maximum number of documents to return.

        Returns:
            List of Documents ordered by relevance score (highest first).
        """
        if not query or not query.strip():
            return []

        # Generate query embedding
        query_embedding = self._embedder.embed(query)

        # Fetch more candidates than needed for re-ranking
        fetch_k = min(top_k * 3, 20)
        candidates = self._store.search(query_embedding, top_k=fetch_k)

        if not candidates:
            return []

        # Score and rank candidates
        scored = self._score_documents(
            candidates, query_embedding, call_stage
        )

        # Sort by score descending and return top_k
        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]

    def _score_documents(
        self,
        documents: list[Document],
        query_embedding: list[float],
        call_stage: str,
    ) -> list[tuple[float, Document]]:
        """Score documents with boosting for compliance, scripts, and stage match.

        Args:
            documents: Candidate documents from vector search.
            query_embedding: The query embedding vector.
            call_stage: Current call stage for stage-match boosting.

        Returns:
            List of (score, document) tuples.
        """
        scored: list[tuple[float, Document]] = []

        for doc in documents:
            # Base similarity score
            if doc.embedding:
                try:
                    base_score = EmbeddingProvider.cosine_similarity(
                        query_embedding, doc.embedding
                    )
                except ValueError:
                    base_score = 0.0
            else:
                base_score = 0.0

            boost = 1.0
            metadata = doc.metadata or {}
            topic = metadata.get("topic", "")
            source_file = metadata.get("source_file", "")
            doc_stage = metadata.get("call_stage", "")

            # Compliance boost
            if topic == "compliance" or "compliance" in source_file.lower():
                boost *= _COMPLIANCE_BOOST

            # Script boost
            if topic == "script" or "script" in source_file.lower():
                boost *= _SCRIPT_BOOST

            # Stage match boost
            if call_stage and doc_stage and doc_stage == call_stage:
                boost *= _STAGE_MATCH_BOOST

            final_score = base_score * boost
            scored.append((final_score, doc))

        return scored
