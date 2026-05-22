"""Vector store backends for RAG knowledge base.

Provides JsonlVectorStore (default, file-based) and PostgresVectorStore
(when DATABASE_URL is set) for document storage and similarity search.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from rag.document import Document
from rag.embeddings import EmbeddingProvider


class BaseVectorStore(ABC):
    """Abstract base class for vector store backends."""

    @abstractmethod
    def add(self, document: Document) -> None:
        """Add a single document to the store."""
        ...

    @abstractmethod
    def search(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[Document]:
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
    """File-based vector store using JSONL format.

    Stores documents as newline-delimited JSON in a single file.
    Performs brute-force cosine similarity search (suitable for
    small-to-medium knowledge bases).

    Args:
        path: Path to the JSONL file. Defaults to ``data/vector_index.jsonl``.
    """

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
            # If file is corrupted, start fresh
            self._documents = []

    def add(self, document: Document) -> None:
        """Append a document to the store and persist to disk."""
        self._ensure_loaded()
        self._documents.append(document)
        self._persist_single(document)

    def search(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[Document]:
        """Brute-force cosine similarity search.

        Args:
            query_embedding: The query vector.
            top_k: Number of top results to return.

        Returns:
            List of most similar documents, ordered by similarity (desc).
        """
        self._ensure_loaded()

        if not self._documents or not query_embedding:
            return []

        scored: list[tuple[float, Document]] = []
        for doc in self._documents:
            if doc.embedding is None:
                continue
            try:
                sim = EmbeddingProvider.cosine_similarity(
                    query_embedding, doc.embedding
                )
                scored.append((sim, doc))
            except ValueError:
                # Dimension mismatch — skip
                continue

        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]

    def build_index(self, documents: list[Document]) -> None:
        """Rebuild the full index, replacing all existing documents.

        Args:
            documents: Complete list of documents to store.
        """
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
    """PostgreSQL-backed vector store using pgvector.

    Requires DATABASE_URL environment variable and the ``pgvector``
    extension. This is a placeholder implementation — full pgvector
    integration should be completed when deploying to production.

    Args:
        database_url: PostgreSQL connection string. If None, reads
            from DATABASE_URL environment variable.
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        self._database_url = database_url or os.environ.get("DATABASE_URL", "")
        if not self._database_url:
            raise ValueError(
                "PostgresVectorStore requires DATABASE_URL to be set"
            )
        # Connection will be established lazily
        self._conn = None

    def _get_connection(self):
        """Get or create database connection."""
        if self._conn is None:
            try:
                import psycopg2  # type: ignore

                self._conn = psycopg2.connect(self._database_url)
                self._ensure_table()
            except ImportError:
                raise ImportError(
                    "psycopg2 is required for PostgresVectorStore. "
                    "Install with: pip install psycopg2-binary"
                )
        return self._conn

    def _ensure_table(self) -> None:
        """Create the documents table if it doesn't exist."""
        conn = self._conn
        if conn is None:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    source TEXT,
                    chunk_type TEXT,
                    metadata JSONB,
                    embedding FLOAT8[],
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.commit()

    def add(self, document: Document) -> None:
        """Insert a document into PostgreSQL."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag_documents (id, content, source, chunk_type, metadata, embedding, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding
                """,
                (
                    document.id,
                    document.content,
                    document.source,
                    document.chunk_type,
                    json.dumps(document.metadata),
                    document.embedding,
                    document.created_at.isoformat(),
                ),
            )
            conn.commit()

    def search(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[Document]:
        """Search using cosine similarity in PostgreSQL.

        Note: For production, use pgvector's native cosine distance operator.
        This fallback computes similarity in Python for compatibility.
        """
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id, content, source, chunk_type, metadata, embedding, created_at FROM rag_documents")
            rows = cur.fetchall()

        scored: list[tuple[float, Document]] = []
        for row in rows:
            doc = Document(
                id=row[0],
                content=row[1],
                source=row[2] or "",
                chunk_type=row[3] or "paragraph",
                metadata=row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
                embedding=row[5],
                created_at=row[6],
            )
            if doc.embedding:
                try:
                    sim = EmbeddingProvider.cosine_similarity(
                        query_embedding, doc.embedding
                    )
                    scored.append((sim, doc))
                except ValueError:
                    continue

        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]

    def build_index(self, documents: list[Document]) -> None:
        """Rebuild the full index in PostgreSQL."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rag_documents")
            for doc in documents:
                cur.execute(
                    """
                    INSERT INTO rag_documents (id, content, source, chunk_type, metadata, embedding, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        doc.id,
                        doc.content,
                        doc.source,
                        doc.chunk_type,
                        json.dumps(doc.metadata),
                        doc.embedding,
                        doc.created_at.isoformat(),
                    ),
                )
            conn.commit()

    def count(self) -> int:
        """Return the number of stored documents."""
        conn = self._get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM rag_documents")
            result = cur.fetchone()
            return result[0] if result else 0


def get_vector_store(path: Optional[str] = None) -> BaseVectorStore:
    """Factory function to get the appropriate vector store backend.

    Returns PostgresVectorStore if DATABASE_URL is set, otherwise
    returns JsonlVectorStore.

    Args:
        path: Optional path for JsonlVectorStore file.

    Returns:
        A vector store instance.
    """
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return PostgresVectorStore(database_url)
    return JsonlVectorStore(path)
