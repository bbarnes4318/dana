"""RAG Document model for Dana voice agent.

Defines the Document pydantic model used throughout the RAG pipeline
for chunking, embedding, storage, and retrieval.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class Document(BaseModel):
    """A single chunk of knowledge-base content with embedding and metadata.

    Attributes:
        id: Unique identifier for the document chunk.
        content: The text content of the chunk.
        source: Filepath or origin of the source material.
        chunk_type: Type of chunk — 'heading', 'paragraph', or 'list'.
        metadata: Structured metadata extracted from the source.
        embedding: Optional vector embedding of the content.
        created_at: Timestamp when the document was created.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    source: str
    chunk_type: str = "paragraph"  # 'heading' | 'paragraph' | 'list'
    metadata: dict = Field(default_factory=lambda: {
        "heading": "",
        "section": "",
        "source_file": "",
        "call_stage": "",
        "topic": "",
    })
    embedding: Optional[list[float]] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    model_config = {"arbitrary_types_allowed": True}
