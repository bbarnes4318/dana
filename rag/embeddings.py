"""Embedding provider for RAG knowledge base.

Supports multiple embedding backends (deterministic fallback, legacy TF-IDF,
sentence-transformers, and OpenAI) via a clean provider interface.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from abc import ABC, abstractmethod
from collections import Counter
from typing import Any, Optional


class BaseEmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text."""
        ...

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text."""
        ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for multiple texts."""
        ...

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b):
            raise ValueError(
                f"Vector dimensions must match: {len(a)} != {len(b)}"
            )

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return dot / (norm_a * norm_b)


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    text = text.lower()
    tokens = re.findall(r"\b[a-z0-9]+\b", text)
    return tokens


class _SimpleWordFrequencyProvider(BaseEmbeddingProvider):
    """Fallback embedding using simple word frequency vectors."""

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}
        self._vocab_size: int = 0

    def _build_vocab(self, texts: list[str]) -> None:
        """Build vocabulary from a list of texts."""
        all_tokens: set[str] = set()
        for text in texts:
            all_tokens.update(_tokenize(text))
        self._vocab = {token: i for i, token in enumerate(sorted(all_tokens))}
        self._vocab_size = len(self._vocab)

    def _text_to_vector(self, text: str) -> list[float]:
        """Convert text to a term-frequency vector."""
        if self._vocab_size == 0:
            self._build_vocab([text])

        tokens = _tokenize(text)
        counts = Counter(tokens)
        total = len(tokens) if tokens else 1

        vector = [0.0] * self._vocab_size
        for token, count in counts.items():
            if token in self._vocab:
                vector[self._vocab[token]] = count / total

        return vector

    def embed_text(self, text: str) -> list[float]:
        """Generate a single embedding vector."""
        text = text[:10000]
        if self._vocab_size == 0:
            self._build_vocab([text])
        return self._text_to_vector(text)

    def embed(self, text: str) -> list[float]:
        return self.embed_text(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        if not texts:
            return []
        cleaned = [t[:10000] for t in texts]
        self._build_vocab(cleaned)
        return [self._text_to_vector(t) for t in cleaned]


class _TfidfProvider(BaseEmbeddingProvider):
    """Embedding provider using sklearn's TfidfVectorizer."""

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(
            max_features=5000,
            stop_words="english",
            sublinear_tf=True,
        )
        self._is_fitted: bool = False

    def embed_text(self, text: str) -> list[float]:
        """Generate a single embedding."""
        text = text[:10000]
        if not self._is_fitted:
            matrix = self._vectorizer.fit_transform([text])
            self._is_fitted = True
        else:
            matrix = self._vectorizer.transform([text])
        return matrix.toarray()[0].tolist()

    def embed(self, text: str) -> list[float]:
        return self.embed_text(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate batch embeddings."""
        if not texts:
            return []
        cleaned = [t[:10000] for t in texts]
        matrix = self._vectorizer.fit_transform(cleaned)
        self._is_fitted = True
        return [row.tolist() for row in matrix.toarray()]


class DeterministicFallbackProvider(BaseEmbeddingProvider):
    """Deterministic, stable fallback embedding provider using hashing.

    Guarantees stable dimension sizes and vectors without network calls,
    heavy libraries, or fit/vocab state.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self.name = "deterministic"
        self.dimensions = dimensions

    def embed_text(self, text: str) -> list[float]:
        """Generates a stable MD5 token hash vector."""
        text = text[:10000]
        if not text.strip():
            return [0.0] * self.dimensions

        tokens = _tokenize(text)
        if not tokens:
            return [0.0] * self.dimensions

        vector = [0.0] * self.dimensions
        for token in tokens:
            # MD5 is stable across runs and processes
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dimensions
            vector[idx] += 1.0

        # Normalize L2
        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0.0:
            vector = [x / norm for x in vector]

        return vector

    def embed(self, text: str) -> list[float]:
        return self.embed_text(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [self.embed_text(t) for t in texts]


class SentenceTransformersProvider(BaseEmbeddingProvider):
    """SentenceTransformers embedding provider using local models."""

    def __init__(self, model_name: str | None = None) -> None:
        self.name = "sentence_transformers"
        self.model_name = model_name or os.environ.get("DANA_EMBEDDING_MODEL") or "all-MiniLM-L6-v2"
        
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "The 'sentence-transformers' package is required for SentenceTransformers provider. "
                "Install it using 'pip install sentence-transformers'."
            )
        self._model = SentenceTransformer(self.model_name)
        self.dimensions = getattr(self._model, "get_sentence_embedding_dimension", lambda: 384)()

    def embed_text(self, text: str) -> list[float]:
        text = text[:10000]
        if not text.strip():
            return [0.0] * self.dimensions
        res = self.embed_batch([text])
        return res[0]

    def embed(self, text: str) -> list[float]:
        return self.embed_text(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        cleaned = [t[:10000] if t.strip() else " " for t in texts]
        embeddings = self._model.encode(cleaned)
        return [em.tolist() for em in embeddings]


class OpenAIProvider(BaseEmbeddingProvider):
    """OpenAI API embedding provider."""

    def __init__(self, api_key: str | None = None, model: str | None = None, dimensions: int | None = None) -> None:
        self.name = "openai"
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required for OpenAI provider.")
        self.model = model or os.environ.get("DANA_EMBEDDING_MODEL") or "text-embedding-3-small"
        
        explicit_dims = os.environ.get("DANA_EMBEDDING_DIMENSIONS")
        if dimensions is not None:
            self.dimensions = dimensions
        elif explicit_dims is not None:
            self.dimensions = int(explicit_dims)
        else:
            rag_backend = os.environ.get("DANA_RAG_BACKEND")
            emb_provider = os.environ.get("DANA_EMBEDDING_PROVIDER")
            if rag_backend == "postgres" and emb_provider == "openai":
                self.dimensions = 384
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    "Postgres RAG backend configured with OpenAI embedding provider. "
                    "Defaulting OpenAIProvider dimensions to 384 to match Postgres 'rag_documents' vector(384) table schema. "
                    "Set DANA_EMBEDDING_DIMENSIONS explicitly to override."
                )
            else:
                self.dimensions = 1536

    def embed_text(self, text: str) -> list[float]:
        text = text[:10000]
        if not text.strip():
            return [0.0] * self.dimensions
        res = self.embed_batch([text])
        return res[0] if res else [0.0] * self.dimensions

    def embed(self, text: str) -> list[float]:
        return self.embed_text(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        
        cleaned = []
        for t in texts:
            c = t[:10000]
            if not c.strip():
                c = " "  # OpenAI fails on empty text
            cleaned.append(c)

        try:
            import openai
        except ImportError:
            raise ImportError("The 'openai' package is required for OpenAI embedding provider. Install it using 'pip install openai'.")

        client = openai.OpenAI(api_key=self.api_key)
        kwargs = {"input": cleaned, "model": self.model}
        if "text-embedding-3" in self.model and self.dimensions:
            kwargs["dimensions"] = self.dimensions

        response = client.embeddings.create(**kwargs)
        data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in data]


class LegacyCompatibilityProvider(BaseEmbeddingProvider):
    """Wraps legacy TF-IDF or simple word frequency backends."""

    def __init__(self) -> None:
        self.name = "legacy"
        try:
            self._backend = _TfidfProvider()
            self._backend_name = "tfidf"
        except ImportError:
            self._backend = _SimpleWordFrequencyProvider()
            self._backend_name = "word_frequency"
        self.dimensions = None

    def embed_text(self, text: str) -> list[float]:
        return self._backend.embed(text)

    def embed(self, text: str) -> list[float]:
        return self.embed_text(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._backend.embed_batch(texts)


class EmbeddingProvider(BaseEmbeddingProvider):
    """Smart wrapper client providing the selected embedding provider backend."""

    def __init__(self, backend_or_name: Optional[Any] = None) -> None:
        self._provider: Optional[BaseEmbeddingProvider] = None

        provider_name = os.environ.get("DANA_EMBEDDING_PROVIDER")
        if isinstance(backend_or_name, str):
            provider_name = backend_or_name
        elif isinstance(backend_or_name, BaseEmbeddingProvider):
            self._provider = backend_or_name
            return

        if not self._provider:
            if provider_name == "openai":
                self._provider = OpenAIProvider()
            elif provider_name == "sentence_transformers":
                self._provider = SentenceTransformersProvider()
            elif provider_name == "legacy":
                self._provider = LegacyCompatibilityProvider()
            elif provider_name == "deterministic":
                dim = int(os.environ.get("DANA_EMBEDDING_DIMENSIONS")) if os.environ.get("DANA_EMBEDDING_DIMENSIONS") else 384
                self._provider = DeterministicFallbackProvider(dimensions=dim)
            else:
                dim = int(os.environ.get("DANA_EMBEDDING_DIMENSIONS")) if os.environ.get("DANA_EMBEDDING_DIMENSIONS") else 384
                self._provider = DeterministicFallbackProvider(dimensions=dim)

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def dimensions(self) -> int | None:
        return self._provider.dimensions

    def embed_text(self, text: str) -> list[float]:
        return self._provider.embed_text(text)

    def embed(self, text: str) -> list[float]:
        return self._provider.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._provider.embed_batch(texts)

    @property
    def backend_name(self) -> str:
        if hasattr(self._provider, "_backend_name"):
            return self._provider._backend_name
        return self._provider.name


def get_embedding_provider(provider_name: str | None = None) -> EmbeddingProvider:
    """Factory to obtain an EmbeddingProvider instance."""
    return EmbeddingProvider(backend_or_name=provider_name)
