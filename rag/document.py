"""RAG Document model for Dana voice agent.

Defines the Document Pydantic model used throughout the RAG pipeline
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
        source_id: Optional source database identifier.
        source_type: Optional source document type.
        topic: Optional domain topic.
        call_stage: Optional associated call stage.
        doc_type: Optional category of document.
        approved: Whether the document is approved for RAG usage.
        quality_score: Internal evaluation score (0.0 to 1.0 or 0.0 to 10.0).
        compliance_priority: True if compliance rules dictate this context.
        version: Document version trace.
        created_at: Timestamp when the document was created.
        updated_at: Timestamp when the document was last updated.
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
    source_id: Optional[str] = None
    source_type: Optional[str] = None
    topic: Optional[str] = None
    call_stage: Optional[str] = None
    doc_type: Optional[str] = None
    approved: bool = False
    quality_score: Optional[float] = None
    compliance_priority: Optional[bool] = False
    version: Optional[str] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    model_config = {"arbitrary_types_allowed": True}
