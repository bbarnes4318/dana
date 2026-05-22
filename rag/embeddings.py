"""Embedding provider for RAG knowledge base.

Uses TF-IDF as the default lightweight embedding method. Falls back to
simple word-frequency vectors if scikit-learn is not available. Designed
to be swappable for a real embedding model (e.g., sentence-transformers).
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from typing import Optional


class BaseEmbeddingProvider(ABC):
    """Abstract base class for embedding providers.

    Subclass this to swap in a real embedding model (e.g., OpenAI,
    sentence-transformers) while keeping the same interface.
    """

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text."""
        ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for multiple texts."""
        ...

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors.

        Args:
            a: First vector.
            b: Second vector.

        Returns:
            Cosine similarity in range [-1, 1]. Returns 0.0 for zero vectors.
        """
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
    """Fallback embedding using simple word frequency vectors.

    Builds a vocabulary from all seen texts and represents each text
    as a normalized term-frequency vector.
    """

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
            # Build vocab from just this text
            self._build_vocab([text])

        tokens = _tokenize(text)
        counts = Counter(tokens)
        total = len(tokens) if tokens else 1

        vector = [0.0] * self._vocab_size
        for token, count in counts.items():
            if token in self._vocab:
                vector[self._vocab[token]] = count / total

        return vector

    def embed(self, text: str) -> list[float]:
        """Generate a word-frequency embedding for a single text."""
        if self._vocab_size == 0:
            self._build_vocab([text])
        return self._text_to_vector(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate word-frequency embeddings for multiple texts."""
        self._build_vocab(texts)
        return [self._text_to_vector(t) for t in texts]


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

    def embed(self, text: str) -> list[float]:
        """Generate a TF-IDF embedding for a single text."""
        if not self._is_fitted:
            matrix = self._vectorizer.fit_transform([text])
            self._is_fitted = True
        else:
            matrix = self._vectorizer.transform([text])
        return matrix.toarray()[0].tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate TF-IDF embeddings for multiple texts.

        Re-fits the vectorizer on the full corpus for best results.
        """
        matrix = self._vectorizer.fit_transform(texts)
        self._is_fitted = True
        return [row.tolist() for row in matrix.toarray()]


class EmbeddingProvider(BaseEmbeddingProvider):
    """Smart embedding provider that uses TF-IDF if sklearn is available,
    otherwise falls back to simple word-frequency vectors.

    This class delegates to the best available backend and exposes a
    unified interface. To swap for a real model, subclass
    BaseEmbeddingProvider and replace this class.

    Example::

        provider = EmbeddingProvider()
        vec = provider.embed("What is final expense insurance?")
        vecs = provider.embed_batch(["text one", "text two"])
        sim = provider.cosine_similarity(vecs[0], vecs[1])
    """

    def __init__(self, backend: Optional[BaseEmbeddingProvider] = None) -> None:
        if backend is not None:
            self._backend = backend
        else:
            try:
                self._backend = _TfidfProvider()
                self._backend_name = "tfidf"
            except ImportError:
                self._backend = _SimpleWordFrequencyProvider()
                self._backend_name = "word_frequency"

    @property
    def backend_name(self) -> str:
        """Name of the active backend."""
        return getattr(self, "_backend_name", self._backend.__class__.__name__)

    def embed(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text."""
        return self._backend.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for multiple texts."""
        return self._backend.embed_batch(texts)
