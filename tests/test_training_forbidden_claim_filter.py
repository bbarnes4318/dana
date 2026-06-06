"""Test to verify that compliance safety filters block approval of forbidden claims."""

from __future__ import annotations

import pytest
from storage.repository import Repository
from training.review_queue import check_lesson_compliance, approve_note


@pytest.mark.asyncio
async def test_compliance_filter_detects_forbidden_claims():
    """check_lesson_compliance must detect and report forbidden claims."""
    # 1. Price/premium quote
    v1 = check_lesson_compliance("Objection rebuttal", "Your monthly cost will be $50 per month.")
    assert len(v1) > 0
    assert any("monthly cost details" in msg or "Specific premium quote" in msg for msg in v1)

    # 2. Promised approval
    v2 = check_lesson_compliance("Promising approval", "Don't worry, you are approved for coverage.")
    assert len(v2) > 0
    assert any("approved" in msg.lower() for msg in v2)

    # 3. Licensed-agent claim
    v3 = check_lesson_compliance("AI licensing claim", "I am a licensed agent with American Beneficiary.")
    assert len(v3) > 0
    assert any("licensed" in msg.lower() for msg in v3)

    # 4. Human claim
    v4 = check_lesson_compliance("AI human claim", "Yes, I am a real person.")
    assert len(v4) > 0
    assert any("human" in msg.lower() or "real person" in msg.lower() for msg in v4)

    # 5. Sensitive data request
    v5 = check_lesson_compliance("Sensitive request", "Could you give me your date of birth?")
    assert len(v5) > 0
    assert any("sensitive" in msg.lower() or "date of birth" in msg.lower() for msg in v5)


@pytest.mark.asyncio
async def test_approve_note_fails_on_non_compliant_content(tmp_path):
    """approve_note CLI action must exit with an error code and not approve a non-compliant note."""
    repo = Repository(data_dir=tmp_path)

    # Create a non-compliant training note
    note_id = await repo.save_training_note(
        source="transcript",
        topic="objection_handling",
        sales_lesson="Quote a great premium!",
        good_response_example="It only costs $29.99 a month.",
        use_in_live_call=False,
        status="pending_review"
    )

    # Mock Repository instantiation in review_queue to use our temp repo
    import unittest.mock as mock
    with mock.patch("training.review_queue.Repository", return_value=repo):
        # Running approve_note on a non-compliant note must raise SystemExit
        with pytest.raises(SystemExit) as excinfo:
            await approve_note(note_id)
        
        # Verify it exited with code 1
        assert excinfo.value.code == 1

        # Verify the note status remained 'pending_review'
        note = await repo.get_training_note(note_id)
        assert note["status"] == "pending_review"
        assert note["use_in_live_call"] is False
