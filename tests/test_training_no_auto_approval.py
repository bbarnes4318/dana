"""Test to verify suggested training notes default to pending_review and do not auto-approve."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from storage.repository import Repository
from main import suggest_lessons_from_call
from training.approved_lessons import get_approved_lessons
from qa.call_record import CallRecord, CallTurn
from qa.scoring import QAScorecard


@pytest.mark.asyncio
async def test_suggested_lessons_no_auto_approval(tmp_path):
    """Suggested lessons must default to use_in_live_call = False and pending_review status."""
    # Initialize repository with a clean JSONL storage
    repo = Repository(data_dir=tmp_path)

    # Reconstruct a high-performing call record
    turns = [
        CallTurn(speaker="prospect", text="I already have final expense coverage", stage="objection"),
        CallTurn(speaker="agent", text="I completely understand, most folks we speak with do, and we just check if we can get you more benefits for less.", stage="objection"),
        CallTurn(speaker="prospect", text="Yes, I am open to reviewing my options.", stage="qualifying"),
        CallTurn(speaker="agent", text="Great, let's get started. Are you living independently?", stage="qualifying"),
    ]
    call_record = CallRecord(
        call_id="call-high-123",
        turns=turns,
        lead_profile={"lead_id": "lead-1", "phone_e164": "+13055550199", "do_not_call_requested": False},
        final_stage="qualifying",
        duration_seconds=45.0,
        tool_events=[]
    )
    scorecard = QAScorecard(
        call_id="call-high-123",
        scores={"objection_handling": 9.5, "compliance_safety": 10.0},
        overall_score=9.5,
        grade="A"
    )

    # Act: generate suggested lessons
    await suggest_lessons_from_call(
        call_record=call_record,
        scorecard=scorecard,
        repository=repo
    )

    # Assert: query notes from repo
    notes = await repo.query_training_notes({})
    assert len(notes) > 0

    # Every suggested note must default to use_in_live_call = False and status = 'pending_review'
    for note in notes:
        assert note["use_in_live_call"] is False
        assert note["status"] == "pending_review"
        assert note["source"] == "call:call-high-123"

    # Assert: get_approved_lessons should return nothing
    approved = await get_approved_lessons(repo)
    assert len(approved) == 0
