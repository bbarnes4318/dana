"""Markdown chunking for RAG knowledge base.

Splits markdown documents into semantically meaningful chunks preserving
heading hierarchy, with automatic sub-splitting for oversized chunks.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from rag.document import Document


# Heading patterns that map to known topics for metadata enrichment
_TOPIC_PATTERNS: dict[str, list[str]] = {
    "objection": ["objection", "pushback", "concern", "hesitation", "refuse"],
    "compliance": ["compliance", "legal", "regulation", "tcpa", "dnc", "forbidden", "boundary"],
    "opening": ["opening", "introduction", "greeting", "warm intro"],
    "permission": ["permission", "consent", "verbal"],
    "age": ["age", "qualify", "qualification"],
    "state": ["state", "location", "verification"],
    "phone_type": ["phone type", "cell", "landline"],
    "text_capable": ["text", "sms"],
    "budget": ["budget", "cost", "price", "afford", "premium"],
    "beneficiary": ["beneficiary", "family", "loved one"],
    "interest": ["interest", "confirm"],
    "transfer": ["transfer", "agent", "licensed"],
    "coverage": ["coverage", "policy", "plan", "final expense", "insurance"],
    "script": ["script", "talk track", "phrase"],
}


def _detect_topic(heading: str) -> str:
    """Detect the topic from a heading string by matching known patterns."""
    heading_lower = heading.lower()
    for topic, patterns in _TOPIC_PATTERNS.items():
        for pattern in patterns:
            if pattern in heading_lower:
                return topic
    return ""


def _detect_chunk_type(text: str) -> str:
    """Detect chunk type based on content patterns."""
    lines = text.strip().split("\n")
    list_pattern = re.compile(r"^\s*[-*+]\s|^\s*\d+\.\s")
    list_lines = sum(1 for line in lines if list_pattern.match(line))

    if list_lines > len(lines) * 0.5 and list_lines >= 2:
        return "list"

    heading_pattern = re.compile(r"^#{1,6}\s")
    if lines and heading_pattern.match(lines[0]) and len(lines) <= 2:
        return "heading"

    return "paragraph"


def _extract_source_file(source: str) -> str:
    """Extract just the filename from a source path."""
    return source.replace("\\", "/").rsplit("/", 1)[-1] if source else ""


class MarkdownChunker:
    """Splits markdown text into semantically meaningful chunks.

    Chunks are split on heading boundaries, with oversized chunks
    sub-split at paragraph boundaries. Each chunk preserves its
    heading hierarchy for context.

    Args:
        max_chunk_size: Maximum character count per chunk (default 500).
    """

    def __init__(self, max_chunk_size: int = 500) -> None:
        self.max_chunk_size = max_chunk_size

    def chunk_markdown(self, text: str, source: str = "") -> list[Document]:
        """Split markdown text into Document chunks.

        Args:
            text: Raw markdown text to chunk.
            source: Filepath or identifier for the source document.

        Returns:
            List of Document objects, one per chunk.
        """
        if not text or not text.strip():
            return []

        sections = self._split_by_heading(text)
        documents: list[Document] = []

        for heading_hierarchy, content in sections:
            full_heading = " > ".join(heading_hierarchy) if heading_hierarchy else ""
            topic = ""
            for h in heading_hierarchy:
                detected = _detect_topic(h)
                if detected:
                    topic = detected
                    break

            # Determine call_stage from topic if it maps to a stage
            call_stage = self._topic_to_call_stage(topic)
            section_name = heading_hierarchy[-1] if heading_hierarchy else ""

            if len(content) <= self.max_chunk_size:
                doc = self._make_document(
                    content=content,
                    source=source,
                    heading=full_heading,
                    section=section_name,
                    call_stage=call_stage,
                    topic=topic,
                )
                documents.append(doc)
            else:
                sub_chunks = self._sub_split(content)
                for i, sub in enumerate(sub_chunks):
                    doc = self._make_document(
                        content=sub,
                        source=source,
                        heading=full_heading,
                        section=f"{section_name} (part {i + 1})" if len(sub_chunks) > 1 else section_name,
                        call_stage=call_stage,
                        topic=topic,
                    )
                    documents.append(doc)

        return documents

    def _split_by_heading(self, text: str) -> list[tuple[list[str], str]]:
        """Split text on markdown headings, tracking heading hierarchy.

        Returns:
            List of (heading_hierarchy, content) tuples.
        """
        heading_re = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
        sections: list[tuple[list[str], str]] = []

        # Track current heading hierarchy by level
        heading_stack: dict[int, str] = {}
        last_pos = 0
        last_hierarchy: list[str] = []

        matches = list(heading_re.finditer(text))

        if not matches:
            # No headings — return entire text as one chunk
            return [([], text.strip())]

        # Content before first heading
        pre_content = text[:matches[0].start()].strip()
        if pre_content:
            sections.append(([], pre_content))

        for i, match in enumerate(matches):
            level = len(match.group(1))
            heading_text = match.group(2).strip()

            # Update heading stack
            heading_stack[level] = heading_text
            # Remove deeper headings from stack
            for deeper in list(heading_stack.keys()):
                if deeper > level:
                    del heading_stack[deeper]

            # Build hierarchy from stack
            hierarchy = [
                heading_stack[lvl]
                for lvl in sorted(heading_stack.keys())
            ]

            # Get content between this heading and the next
            content_start = match.end()
            if i + 1 < len(matches):
                content_end = matches[i + 1].start()
            else:
                content_end = len(text)

            content = text[content_start:content_end].strip()

            # Include the heading in the content for context
            full_content = f"{match.group(0).strip()}\n\n{content}" if content else match.group(0).strip()

            if full_content.strip():
                sections.append((hierarchy, full_content.strip()))

        return sections

    def _sub_split(self, text: str) -> list[str]:
        """Split oversized text at paragraph boundaries.

        Args:
            text: Text that exceeds max_chunk_size.

        Returns:
            List of sub-chunks, each within max_chunk_size.
        """
        paragraphs = re.split(r"\n\s*\n", text)
        chunks: list[str] = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            candidate = f"{current}\n\n{para}".strip() if current else para

            if len(candidate) <= self.max_chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                # If single paragraph exceeds limit, force-add it
                if len(para) > self.max_chunk_size:
                    # Split by sentences as last resort
                    sentence_chunks = self._split_by_sentences(para)
                    chunks.extend(sentence_chunks)
                    current = ""
                else:
                    current = para

        if current:
            chunks.append(current)

        return chunks if chunks else [text]

    def _split_by_sentences(self, text: str) -> list[str]:
        """Last-resort splitting by sentence boundaries."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks: list[str] = []
        current = ""

        for sentence in sentences:
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= self.max_chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sentence

        if current:
            chunks.append(current)

        return chunks if chunks else [text]

    def _make_document(
        self,
        content: str,
        source: str,
        heading: str,
        section: str,
        call_stage: str,
        topic: str,
    ) -> Document:
        """Create a Document with populated metadata."""
        return Document(
            id=str(uuid.uuid4()),
            content=content,
            source=source,
            chunk_type=_detect_chunk_type(content),
            metadata={
                "heading": heading,
                "section": section,
                "source_file": _extract_source_file(source),
                "call_stage": call_stage,
                "topic": topic,
            },
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _topic_to_call_stage(topic: str) -> str:
        """Map a detected topic to a CallStage value string."""
        stage_map = {
            "opening": "opening",
            "permission": "permission",
            "age": "age",
            "state": "state",
            "phone_type": "phone_type",
            "text_capable": "text_capable",
            "budget": "budget",
            "beneficiary": "beneficiary",
            "interest": "interest",
            "transfer": "transfer_ready",
            "objection": "objection",
            "compliance": "",
            "coverage": "",
            "script": "",
        }
        return stage_map.get(topic, "")
