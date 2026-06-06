"""Utilities for retrieving approved training notes/lessons from repository."""

from __future__ import annotations

from typing import Optional
from storage.repository import Repository


async def get_approved_lessons(repository: Optional[Repository] = None) -> list[dict]:
    """Retrieve all training notes/lessons that are approved for RAG usage.

    Checks for status='approved' or use_in_live_call=True.
    """
    repo = repository or Repository()

    # Query by status
    approved_by_status = await repo.query_training_notes({"status": "approved"})

    # Query by use_in_live_call
    approved_by_flag = await repo.query_training_notes({"use_in_live_call": True})

    # Merge and deduplicate
    seen_ids = set()
    merged = []

    for note in approved_by_status + approved_by_flag:
        note_id = note.get("id")
        if note_id and note_id not in seen_ids:
            seen_ids.add(note_id)
            merged.append(note)

    return merged
