"""Test to verify review queue listing, approval of compliant notes, and rejections."""

from __future__ import annotations

import pytest
import unittest.mock as mock
from storage.repository import Repository
from training.review_queue import list_pending, approve_note, reject_note


@pytest.mark.asyncio
async def test_review_queue_list_pending(tmp_path, capsys):
    """list_pending must output all notes with pending_review status."""
    repo = Repository(data_dir=tmp_path)

    # Save a pending note and an approved note
    await repo.save_training_note(
        source="transcript-1",
        topic="objection_handling",
        sales_lesson="Handle pushback warmly",
        good_response_example="I understand, let's look at coverage options.",
        status="pending_review"
    )
    await repo.save_training_note(
        source="transcript-2",
        topic="opening",
        sales_lesson="Greet the customer",
        good_response_example="Hello this is Alex.",
        status="approved"
    )

    with mock.patch("training.review_queue.Repository", return_value=repo):
        await list_pending()
        
        captured = capsys.readouterr()
        assert "Pending Review Queue" in captured.out
        assert "Handle pushback warmly" in captured.out
        assert "Greet the customer" not in captured.out


@pytest.mark.asyncio
async def test_review_queue_approve_compliant_note(tmp_path):
    """approve_note must approve a compliant note successfully."""
    repo = Repository(data_dir=tmp_path)

    note_id = await repo.save_training_note(
        source="transcript",
        topic="objection_handling",
        sales_lesson="Handle objections warmly",
        good_response_example="I understand and appreciate you sharing that.",
        status="pending_review",
        use_in_live_call=False
    )

    with mock.patch("training.review_queue.Repository", return_value=repo):
        await approve_note(note_id)

        # Verify the note is approved and live-call enabled
        note = await repo.get_training_note(note_id)
        assert note["status"] == "approved"
        assert note["use_in_live_call"] is True


@pytest.mark.asyncio
async def test_review_queue_reject_note(tmp_path):
    """reject_note must reject a note and mark it disabled for live calls."""
    repo = Repository(data_dir=tmp_path)

    note_id = await repo.save_training_note(
        source="transcript",
        topic="objection_handling",
        sales_lesson="Objection lesson",
        good_response_example="Example",
        status="pending_review",
        use_in_live_call=False
    )

    with mock.patch("training.review_queue.Repository", return_value=repo):
        await reject_note(note_id)

        # Verify the note is rejected and live-call disabled
        note = await repo.get_training_note(note_id)
        assert note["status"] == "rejected"
        assert note["use_in_live_call"] is False
