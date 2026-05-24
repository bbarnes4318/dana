"""Context builder for RAG-augmented LLM prompts.

Retrieves relevant documents and formats them into a context block
for injection into Dana's system prompt.
"""

from __future__ import annotations

import logging
from typing import Optional

from rag.document import Document
from rag.embeddings import EmbeddingProvider
from rag.retriever import Retriever
from rag.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)

# Approximate max characters (1200 tokens ~ 4800 chars)
_MAX_CONTEXT_CHARS = 4800

# Priority ordering for document categories
_PRIORITY_ORDER = {
    "compliance": 0,
    "script": 1,
    "objection": 2,
    "coverage": 3,
    "opening": 4,
    "permission": 4,
    "age": 4,
    "state": 4,
    "phone_type": 4,
    "text_capable": 4,
    "budget": 4,
    "beneficiary": 4,
    "interest": 4,
    "transfer": 4,
}


def _get_priority(doc: Document) -> int:
    """Get priority rank for a document (lower = higher priority)."""
    metadata = doc.metadata or {}
    topic = metadata.get("topic", "")
    source_file = metadata.get("source_file", "").lower()

    # Check source file for compliance/script indicators
    if "compliance" in source_file:
        return _PRIORITY_ORDER["compliance"]
    if "script" in source_file:
        return _PRIORITY_ORDER["script"]

    return _PRIORITY_ORDER.get(topic, 10)


class ContextBuilder:
    """Builds context blocks for LLM system prompt injection.

    Retrieves relevant documents based on query, call stage, and
    other parameters, then formats them into a structured text block
    ready for prompt injection.

    Args:
        retriever: Retriever instance. If None, creates a default one.
        max_chars: Maximum character count for the context block.
    """

    def __init__(
        self,
        retriever: Optional[Retriever] = None,
        max_chars: int = _MAX_CONTEXT_CHARS,
    ) -> None:
        self._retriever = retriever or Retriever()
        self._max_chars = max_chars

    def build_context(
        self,
        query: str,
        call_stage: str = "",
        lead_profile: Optional[dict] = None,
        objection_type: Optional[str] = None,
    ) -> str:
        """Build a formatted context block for the LLM.

        Retrieves relevant documents and formats them with priority
        ordering: compliance > stage script > objection > knowledge.

        Args:
            query: The query or current conversation context.
            call_stage: Current call stage name.
            lead_profile: Optional dict of lead profile data for context.
            objection_type: Optional objection type to boost retrieval.

        Returns:
            Formatted context string ready for system prompt injection.
            Empty string if no relevant documents are found.
        """
        if not query or not query.strip():
            return ""

        # Build enhanced query incorporating stage and objection context
        enhanced_query = self._enhance_query(
            query, call_stage, objection_type
        )

        # Retrieve more documents than we might need
        documents = self._retriever.retrieve(
            enhanced_query, call_stage=call_stage, top_k=10
        )

        if not documents:
            return ""

        # Sort by priority
        documents = self._prioritize(documents)

        # Format and truncate to max chars
        context = self._format_context(
            documents, call_stage, lead_profile, objection_type
        )

        return context

    def _enhance_query(
        self,
        query: str,
        call_stage: str,
        objection_type: Optional[str],
    ) -> str:
        """Enhance query with stage and objection context."""
        parts = [query]

        if call_stage:
            parts.append(f"call stage: {call_stage}")

        if objection_type:
            parts.append(f"objection: {objection_type}")

        return " ".join(parts)

    def _prioritize(self, documents: list[Document]) -> list[Document]:
        """Sort documents by priority category.

        Order: compliance > script > objection > general knowledge.
        """
        return sorted(documents, key=_get_priority)

    def _format_context(
        self,
        documents: list[Document],
        call_stage: str,
        lead_profile: Optional[dict],
        objection_type: Optional[str],
    ) -> str:
        """Format documents into a context block, respecting char limit.

        Args:
            documents: Priority-sorted documents.
            call_stage: Current call stage.
            lead_profile: Optional lead profile data.
            objection_type: Optional objection type.

        Returns:
            Formatted context string.
        """
        sections: list[str] = []
        current_length = 0

        # Header
        header = "--- KNOWLEDGE CONTEXT ---"
        current_length += len(header) + 1
        sections.append(header)

        # Stage context line
        if call_stage:
            stage_line = f"Current Stage: {call_stage}"
            current_length += len(stage_line) + 1
            sections.append(stage_line)

        if objection_type:
            obj_line = f"Objection Type: {objection_type}"
            current_length += len(obj_line) + 1
            sections.append(obj_line)

        sections.append("")  # blank line

        # Add documents with source attribution
        for doc in documents:
            metadata = doc.metadata or {}
            source_file = metadata.get("source_file", "unknown")
            topic = metadata.get("topic", "")
            section_name = metadata.get("section", "")

            # Build document entry
            label_parts = [f"[{source_file}]"]
            if topic:
                label_parts.append(f"({topic})")
            if section_name:
                label_parts.append(f"- {section_name}")
            label = " ".join(label_parts)

            entry = f"{label}\n{doc.content}\n"
            entry_length = len(entry)

            # Check if adding this entry would exceed limit
            if current_length + entry_length > self._max_chars:
                # Try to fit a truncated version
                remaining = self._max_chars - current_length - len(label) - 20
                if remaining > 100:
                    truncated_content = doc.content[:remaining].rsplit(" ", 1)[0] + "..."
                    entry = f"{label}\n{truncated_content}\n"
                    sections.append(entry)
                break

            sections.append(entry)
            current_length += entry_length

        sections.append("--- END CONTEXT ---")

        return "\n".join(sections)
