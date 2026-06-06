"""Test to verify reindexing approved training notes into the RAG vector store."""

from __future__ import annotations

import pytest
import os
import unittest.mock as mock
from storage.repository import Repository
from rag.vector_store import get_vector_store
from training.reindex_approved_notes import convert_note_to_document, reindex_notes


@pytest.mark.asyncio
async def test_convert_note_to_document():
    """convert_note_to_document should format document content correctly."""
    note = {
        "id": "note-123",
        "source": "transcript",
        "topic": "objection_handling",
        "sales_lesson": "Handle objection",
        "good_response_example": "Good response",
        "bad_response_example": "Bad response",
        "call_stage": "objection",
        "objection_type": "already_has_coverage",
        "compliance_risk": "low"
    }

    doc = convert_note_to_document(note)
    assert doc.id == "training_note:note-123"
    assert doc.source == "transcript"
    assert doc.doc_type == "training_note"
    assert doc.approved is True
    assert doc.metadata["training_note_id"] == "note-123"
    assert doc.metadata["objection_type"] == "already_has_coverage"
    
    # Check that formatted content includes key parts
    assert "Training Lesson: Handle objection" in doc.content
    assert "Good Response Example:\n\"Good response\"" in doc.content
    assert "Bad Response Example (to avoid):\n\"Bad response\"" in doc.content
    assert "Objection Type: already_has_coverage" in doc.content


@pytest.mark.asyncio
async def test_reindex_notes_saves_to_vector_store(tmp_path):
    """reindex_notes must fetch approved notes, compute embeddings, and save to vector store."""
    repo = Repository(data_dir=tmp_path)

    # Save one approved and one pending note
    await repo.save_training_note(
        source="s1",
        topic="objection_handling",
        sales_lesson="Handle pushback",
        good_response_example="We check if we can get you more benefits.",
        status="approved",
        use_in_live_call=True
    )
    await repo.save_training_note(
        source="s2",
        topic="opening",
        sales_lesson="Greeting",
        good_response_example="Hello",
        status="pending_review",
        use_in_live_call=False
    )

    # Setup temp JSONL vector store path
    vector_store_path = str(tmp_path / "vector_store.jsonl")
    
    # Mock get_vector_store to return store pointing to our temp path
    from rag.vector_store import JsonlVectorStore
    store = JsonlVectorStore(path=vector_store_path)
    
    with mock.patch("training.reindex_approved_notes.get_vector_store", return_value=store):
        await reindex_notes(repository=repo)
        
        # Verify 1 document is successfully added to the store
        assert store.count() == 1
        
        # Search the document in the store using mock query embedding
        # Default dimensions for deterministic provider is 384
        from rag.embeddings import get_embedding_provider
        embedder = get_embedding_provider()
        query_emb = embedder.embed("pushback")
        
        results = store.search(query_embedding=query_emb, approved_only=True)
        assert len(results) == 1
        assert results[0].document.metadata["topic"] == "objection_handling"
        assert "Handle pushback" in results[0].document.content
