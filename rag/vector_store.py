"""Vector store backends for RAG knowledge base.

Provides JsonlVectorStore (default, file-based) and PostgresVectorStore
(when DATABASE_URL is set) for document storage and similarity search.
"""

from __future__ import annotations

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field

from rag.document import Document
from rag.embeddings import EmbeddingProvider, BaseEmbeddingProvider

logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    """The result of a search query, containing the document and detailed score breakdown."""

    document: Document
    score: float
    semantic_score: float
    keyword_score: float
    metadata_score: float
    final_score: float
    reasons: list[str] = Field(default_factory=list)


_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "aren't", "as", "at",
    "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "can't", "cannot",
    "could", "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during", "each", "few",
    "for", "from", "further", "had", "hadn't", "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll",
    "he's", "her", "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll",
    "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "let's", "me", "more", "most",
    "mustn't", "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our",
    "ours", "ourselves", "out", "over", "own", "same", "shan't", "she", "she'd", "she'll", "she's", "should",
    "shouldn't", "so", "some", "such", "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this", "those",
    "through", "to", "too", "under", "until", "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're",
    "we've", "were", "weren't", "what", "what's", "when", "when's", "where", "where's", "which", "while", "who",
    "who's", "whom", "why", "why's", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll", "you're",
    "you've", "your", "yours", "yourself", "yourselves"
}


def _tokenize_and_clean(text: str) -> list[str]:
    """Tokenize and remove stopwords from text."""
    words = re.findall(r"\b[a-z0-9']+\b", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _compute_keyword_score(query_text: str | None, doc_content: str) -> tuple[float, list[str]]:
    """Compute keyword overlap and exact match scoring."""
    if not query_text or not query_text.strip():
        return 0.0, ["No query text provided for keyword matching."]

    query_tokens = _tokenize_and_clean(query_text)
    doc_tokens = set(_tokenize_and_clean(doc_content))

    if not query_tokens:
        return 0.0, ["Query contains only stopwords."]

    overlap = [w for w in query_tokens if w in doc_tokens]
    overlap_ratio = len(overlap) / len(query_tokens)

    score = overlap_ratio
    reasons = [f"Keyword overlap ratio: {len(overlap)}/{len(query_tokens)} ({overlap_ratio:.2f})."]

    # Exact phrase match boost
    if query_text.lower() in doc_content.lower():
        score = min(1.0, score + 0.3)
        reasons.append("Exact phrase match boost applied (+0.3).")

    # Objection specific phrase matches
    objection_keywords = ["don't call", "do not call", "remove me", "licensed", "cost", "price", "real person", "human"]
    for kw in objection_keywords:
        if kw in query_text.lower() and kw in doc_content.lower():
            score = min(1.0, score + 0.2)
            reasons.append(f"Objection keyword '{kw}' matched in query and document (+0.2).")
            break

    return score, reasons


def _compute_hybrid_score(
    doc: Document,
    query_embedding: list[float],
    query_text: str | None,
    call_stage: str | None,
    topic: str | None,
) -> SearchResult:
    """Core hybrid scoring helper that calculates semantic, keyword, and metadata boosts."""
    # 1. Semantic score
    semantic_score = 0.0
    if doc.embedding and query_embedding:
        try:
            # Cosine similarity range is [-1, 1], normalize/clamp to >= 0
            sim = BaseEmbeddingProvider.cosine_similarity(query_embedding, doc.embedding)
            semantic_score = max(0.0, sim)
        except ValueError:
            semantic_score = 0.0

    # 2. Keyword score
    keyword_score = 0.0
    keyword_reasons = []
    if query_text:
        keyword_score, keyword_reasons = _compute_keyword_score(query_text, doc.content)

    # 3. Call Stage match boost (0.10 weight)
    stage_score = 0.0
    stage_reasons = []
    doc_stage = doc.call_stage or doc.metadata.get("call_stage") or doc.metadata.get("stage")
    if call_stage:
        if doc_stage == call_stage:
            stage_score = 1.0
            stage_reasons.append(f"Document stage '{doc_stage}' matches query call_stage.")
        else:
            stage_reasons.append(f"Document stage '{doc_stage}' does not match query call_stage '{call_stage}'.")
    else:
        stage_reasons.append("No query call_stage provided.")

    # 4. Topic match boost (0.05 weight)
    topic_score = 0.0
    topic_reasons = []
    doc_topic = doc.topic or doc.metadata.get("topic")
    if topic:
        if doc_topic == topic:
            topic_score = 1.0
            topic_reasons.append(f"Document topic '{doc_topic}' matches query topic.")
        else:
            topic_reasons.append(f"Document topic '{doc_topic}' does not match query topic '{topic}'.")
    else:
        topic_reasons.append("No query topic provided.")

    # 5. Compliance priority boost (0.05 weight)
    compliance_score = 0.0
    compliance_reasons = []
    is_compliance = doc.compliance_priority or doc.metadata.get("compliance_priority")
    if is_compliance:
        query_lower = (query_text or "").lower()
        compliance_indicators = ["dnc", "stop", "call back", "licensed", "cost", "price", "real person", "human", "remove", "do not call"]
        if any(ind in query_lower for ind in compliance_indicators) or topic == "compliance":
            compliance_score = 1.0
            compliance_reasons.append("Compliance priority document matched compliance/objection query.")
        else:
            compliance_reasons.append("Compliance priority document (query not compliance-related).")
    else:
        compliance_reasons.append("Not a compliance priority document.")

    # 6. Quality score boost (0.05 weight)
    quality_val = doc.quality_score or doc.metadata.get("quality_score")
    quality_score = 0.5  # default neutral
    quality_reasons = []
    if quality_val is not None:
        try:
            q = float(quality_val)
            if q > 1.0:
                q = q / 10.0  # normalize 0-10 to 0-1
            quality_score = max(0.0, min(1.0, q))
            quality_reasons.append(f"Quality score: {quality_score:.2f}.")
        except ValueError:
            quality_reasons.append("Invalid quality_score format; neutral applied.")
    else:
        quality_reasons.append("No quality_score; neutral applied.")

    metadata_score = (
        0.10 * stage_score +
        0.05 * topic_score +
        0.05 * compliance_score +
        0.05 * quality_score
    )

    final_score = (
        0.55 * semantic_score +
        0.20 * keyword_score +
        metadata_score
    )

    reasons = [
        f"Semantic score (weight 0.55): {semantic_score:.4f}."
    ]
    if query_text:
        reasons.extend(keyword_reasons)
    reasons.extend(stage_reasons)
    reasons.extend(topic_reasons)
    reasons.extend(compliance_reasons)
    reasons.extend(quality_reasons)
    reasons.append(f"Final hybrid score: {final_score:.4f}.")

    return SearchResult(
        document=doc,
        score=final_score,
        semantic_score=semantic_score,
        keyword_score=keyword_score,
        metadata_score=metadata_score,
        final_score=final_score,
        reasons=reasons
    )


class BaseVectorStore(ABC):
    """Abstract base class for vector store backends."""

    @abstractmethod
    def add(self, document: Document) -> None:
        """Add a single document to the store."""
        ...

    @abstractmethod
    def search(
        self,
        query_embedding: list[float],
        query_text: str | None = None,
        top_k: int = 5,
        approved_only: bool = True,
        filters: dict | None = None,
        call_stage: str | None = None,
        topic: str | None = None,
        doc_type: str | None = None,
    ) -> list[SearchResult] | list[Document]:
        """Search for most similar documents by embedding."""
        ...

    @abstractmethod
    def build_index(self, documents: list[Document]) -> None:
        """Rebuild the full index from a list of documents."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Return the number of documents in the store."""
        ...


class JsonlVectorStore(BaseVectorStore):
    """File-based vector store using JSONL format."""

    def __init__(self, path: Optional[str] = None) -> None:
        if path is None:
            path = os.path.join("data", "vector_index.jsonl")
        self._path = Path(path)
        self._documents: list[Document] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load documents from disk on first access."""
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        self._documents.append(Document(**data))
        except (json.JSONDecodeError, OSError):
            self._documents = []

    def add(self, document: Document) -> None:
        """Upsert a document in the store and persist to disk."""
        self._ensure_loaded()
        for i, doc in enumerate(self._documents):
            if doc.id == document.id:
                self._documents[i] = document
                self._persist_all()
                return
        self._documents.append(document)
        self._persist_single(document)

    def search(
        self,
        query_embedding: list[float],
        query_text: str | None = None,
        top_k: int = 5,
        approved_only: bool = True,
        filters: dict | None = None,
        call_stage: str | None = None,
        topic: str | None = None,
        doc_type: str | None = None,
    ) -> list[SearchResult]:
        """Brute-force hybrid similarity search."""
        self._ensure_loaded()

        if not self._documents:
            return []

        scored_results: list[SearchResult] = []
        for doc in self._documents:
            # 1. Approved filter
            if approved_only and not doc.approved:
                continue

            # 2. Doc Type hard filter
            if doc_type and doc.doc_type != doc_type:
                continue

            # 3. Hard metadata filters
            match = True
            if filters:
                for k, v in filters.items():
                    if doc.metadata.get(k) != v:
                        match = False
                        break
            if not match:
                continue

            # Compute scores
            res = _compute_hybrid_score(doc, query_embedding, query_text, call_stage, topic)
            scored_results.append(res)

        scored_results.sort(key=lambda x: x.final_score, reverse=True)
        return scored_results[:top_k]

    def build_index(self, documents: list[Document]) -> None:
        """Rebuild the full index, replacing all existing documents."""
        self._documents = list(documents)
        self._loaded = True
        self._persist_all()

    def count(self) -> int:
        """Return the number of stored documents."""
        self._ensure_loaded()
        return len(self._documents)

    def _persist_single(self, document: Document) -> None:
        """Append a single document to the JSONL file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(document.model_dump_json() + "\n")

    def _persist_all(self) -> None:
        """Write all documents to the JSONL file, replacing contents."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            for doc in self._documents:
                f.write(doc.model_dump_json() + "\n")


class PostgresVectorStore(BaseVectorStore):
    """PostgreSQL-backed vector store with optional pgvector support."""

    def __init__(self, database_url: Optional[str] = None) -> None:
        self._database_url = database_url or os.environ.get("DATABASE_URL", "")
        if not self._database_url:
            raise ValueError(
                "PostgresVectorStore requires DATABASE_URL to be set"
            )
        self._conn = None
        self.has_pgvector = False
        # Read default dimension from configuration
        self.dimensions = int(os.environ.get("DANA_EMBEDDING_DIMENSIONS")) if os.environ.get("DANA_EMBEDDING_DIMENSIONS") else 384

    def _get_connection(self):
        """Get or create database connection."""
        if self._conn is None:
            try:
                import psycopg2
                self._conn = psycopg2.connect(self._database_url)
                self._ensure_pgvector_and_table()
            except ImportError:
                raise ImportError(
                    "psycopg2 is required for PostgresVectorStore. "
                    "Install with: pip install psycopg2-binary"
                )
        return self._conn

    def _ensure_pgvector_and_table(self) -> None:
        """Create the documents table if it doesn't exist, enabling pgvector if available."""
        conn = self._conn
        if conn is None:
            return
        
        # Test pgvector availability
        try:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute("SELECT '[1,2,3]'::vector;")
                conn.commit()
                self.has_pgvector = True
        except Exception:
            conn.rollback()
            logger.warning("pgvector extension not available; falling back to FLOAT8[] schema and local similarity scoring.")
            self.has_pgvector = False

        embedding_type = f"vector({self.dimensions})" if (self.has_pgvector and self.dimensions) else "FLOAT8[]"

        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    source TEXT,
                    chunk_type TEXT,
                    metadata JSONB,
                    embedding {embedding_type},
                    source_id TEXT,
                    source_type TEXT,
                    topic TEXT,
                    call_stage TEXT,
                    doc_type TEXT,
                    approved BOOLEAN DEFAULT FALSE,
                    quality_score DOUBLE PRECISION,
                    compliance_priority BOOLEAN DEFAULT FALSE,
                    version TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.commit()

    def add(self, document: Document) -> None:
        """Upsert a document into PostgreSQL."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag_documents (
                    id, content, source, chunk_type, metadata, embedding,
                    source_id, source_type, topic, call_stage, doc_type,
                    approved, quality_score, compliance_priority, version, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    source = EXCLUDED.source,
                    chunk_type = EXCLUDED.chunk_type,
                    metadata = EXCLUDED.metadata,
                    embedding = EXCLUDED.embedding,
                    source_id = EXCLUDED.source_id,
                    source_type = EXCLUDED.source_type,
                    topic = EXCLUDED.topic,
                    call_stage = EXCLUDED.call_stage,
                    doc_type = EXCLUDED.doc_type,
                    approved = EXCLUDED.approved,
                    quality_score = EXCLUDED.quality_score,
                    compliance_priority = EXCLUDED.compliance_priority,
                    version = EXCLUDED.version,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    document.id,
                    document.content,
                    document.source,
                    document.chunk_type,
                    json.dumps(document.metadata),
                    document.embedding,
                    document.source_id,
                    document.source_type,
                    document.topic,
                    document.call_stage,
                    document.doc_type,
                    document.approved,
                    document.quality_score,
                    document.compliance_priority,
                    document.version,
                    document.created_at.isoformat(),
                    document.updated_at.isoformat(),
                ),
            )
            conn.commit()

    def search(
        self,
        query_embedding: list[float],
        query_text: str | None = None,
        top_k: int = 5,
        approved_only: bool = True,
        filters: dict | None = None,
        call_stage: str | None = None,
        topic: str | None = None,
        doc_type: str | None = None,
    ) -> list[SearchResult]:
        """Search using PostgreSQL index and cosine distance where available."""
        conn = self._get_connection()
        
        # Build SQL dynamically
        sql = """
            SELECT id, content, source, chunk_type, metadata, embedding, created_at,
                   source_id, source_type, topic, call_stage, doc_type, approved,
                   quality_score, compliance_priority, version, updated_at
        """
        
        params = []
        where_clauses = []

        if approved_only:
            where_clauses.append("approved = TRUE")

        if doc_type:
            where_clauses.append("doc_type = %s")
            params.append(doc_type)

        if filters:
            for k, v in filters.items():
                if re.match(r"^[a-zA-Z0-9_]+$", k):
                    where_clauses.append("metadata->>%s = %s")
                    params.extend([k, str(v)])

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Fetch more candidates for final Python hybrid re-ranking
        fetch_k = min(top_k * 5, 50)

        if self.has_pgvector:
            # Query pgvector natively
            sql += f" FROM rag_documents {where_sql} ORDER BY embedding <=> %s::vector ASC LIMIT %s"
            params.extend([query_embedding, fetch_k])
        else:
            # Standard select
            sql += f" FROM rag_documents {where_sql}"

        with conn.cursor() as cur:
            if params:
                cur.execute(sql, tuple(params))
            else:
                cur.execute(sql)
            rows = cur.fetchall()

        # Score candidates in Python using the standard hybrid ranker
        scored_results: list[SearchResult] = []
        for row in rows:
            doc = Document(
                id=row[0],
                content=row[1],
                source=row[2] or "",
                chunk_type=row[3] or "paragraph",
                metadata=row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
                embedding=row[5],
                created_at=row[6],
                source_id=row[7],
                source_type=row[8],
                topic=row[9],
                call_stage=row[10],
                doc_type=row[11],
                approved=row[12],
                quality_score=row[13],
                compliance_priority=row[14],
                version=row[15],
                updated_at=row[16],
            )
            res = _compute_hybrid_score(doc, query_embedding, query_text, call_stage, topic)
            scored_results.append(res)

        scored_results.sort(key=lambda x: x.final_score, reverse=True)
        return scored_results[:top_k]

    def build_index(self, documents: list[Document]) -> None:
        """Rebuild the full index in PostgreSQL."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rag_documents")
            conn.commit()
            
        for doc in documents:
            self.add(doc)

    def count(self) -> int:
        """Return the number of stored documents."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM rag_documents")
            result = cur.fetchone()
            return result[0] if result else 0


def get_vector_store(path: Optional[str] = None) -> BaseVectorStore:
    """Factory function to get the appropriate vector store backend."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return PostgresVectorStore(database_url)
    return JsonlVectorStore(path)
