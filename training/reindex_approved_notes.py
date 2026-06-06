"""CLI tool to re-index approved training notes into the RAG vector store."""

from __future__ import annotations

import asyncio
import sys
from typing import Optional

from storage.repository import Repository
from rag.document import Document
from rag.embeddings import get_embedding_provider
from rag.vector_store import get_vector_store
from training.approved_lessons import get_approved_lessons


def convert_note_to_document(note: dict) -> Document:
    """Format and convert a TrainingNote dict to a RAG Document object."""
    note_id = note.get("id")
    sales_lesson = note.get("sales_lesson", "")
    good_example = note.get("good_response_example") or note.get("good_example") or ""
    bad_example = note.get("bad_response_example") or note.get("bad_example") or ""
    topic = note.get("topic", "general_sales")
    call_stage = note.get("call_stage")
    objection_type = note.get("objection_type")
    compliance_risk = note.get("compliance_risk")

    content_parts = [
        f"Training Lesson: {sales_lesson}",
        "",
        f"Topic: {topic}"
    ]
    if call_stage:
        content_parts.append(f"Call Stage: {call_stage}")
    if objection_type:
        content_parts.append(f"Objection Type: {objection_type}")
    if compliance_risk:
        content_parts.append(f"Compliance Risk: {compliance_risk}")

    content_parts.extend([
        "",
        "Good Response Example:",
        f'"{good_example}"'
    ])

    if bad_example:
        content_parts.extend([
            "",
            "Bad Response Example (to avoid):",
            f'"{bad_example}"'
        ])

    content = "\n".join(content_parts)
    doc_id = f"training_note:{note_id}"

    # Build metadata
    tags = ["training_note", "approved"]
    if call_stage:
        tags.append(call_stage)
    if objection_type:
        tags.append(objection_type)

    metadata = {
        "training_note_id": note_id,
        "source": note.get("source"),
        "topic": topic,
        "call_stage": call_stage,
        "objection_type": objection_type,
        "compliance_risk": compliance_risk,
        "created_from": "reindex_approved_notes",
        "tags": tags
    }

    return Document(
        id=doc_id,
        content=content,
        source=note.get("source", "review"),
        source_id=note_id,
        source_type="training_note",
        topic=objection_type if objection_type else (topic if topic else "training_note"),
        call_stage=call_stage,
        doc_type="training_note",
        approved=True,
        quality_score=9.0,
        compliance_priority=(topic == "compliance"),
        version="training-note-v1",
        metadata=metadata
    )


async def reindex_notes(repository: Optional[Repository] = None, dry_run: bool = False):
    """Scan and re-index all approved training notes into the RAG vector store."""
    repo = repository or Repository()

    print("Fetching approved training notes...")
    approved_notes = await get_approved_lessons(repo)
    if not approved_notes:
        print("No approved training notes found to re-index.")
        return

    print(f"Found {len(approved_notes)} approved training note(s).")

    print("Loading embedding provider and vector store...")
    embedder = get_embedding_provider()
    store = get_vector_store()

    for note in approved_notes:
        note_id = note.get("id")
        print(f"Processing training note {note_id}...")

        doc = convert_note_to_document(note)

        # Calculate embedding
        doc.embedding = embedder.embed(doc.content)

        if not dry_run:
            store.add(doc)
            print(f"  Successfully indexed document {doc.id} to vector store.")
        else:
            print(f"  [DRY RUN] Would index document {doc.id}.")

    print("Re-indexing completed successfully.")


def main():
    dry_run = "--dry-run" in sys.argv
    asyncio.run(reindex_notes(dry_run=dry_run))


if __name__ == "__main__":
    main()
