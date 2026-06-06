"""Test to verify get_approved_lessons returns only approved training notes."""

from __future__ import annotations

import pytest
from storage.repository import Repository
from training.approved_lessons import get_approved_lessons


@pytest.mark.asyncio
async def test_get_approved_lessons_returns_only_approved(tmp_path):
    """get_approved_lessons must return notes with status='approved' or use_in_live_call=True."""
    repo = Repository(data_dir=tmp_path)

    # 1. Pending review (should NOT return)
    await repo.save_training_note(
        source="s1",
        topic="t1",
        sales_lesson="lesson1",
        good_response_example="example1",
        status="pending_review",
        use_in_live_call=False
    )

    # 2. Approved (should return)
    note_id_approved = await repo.save_training_note(
        source="s2",
        topic="t2",
        sales_lesson="lesson2",
        good_response_example="example2",
        status="approved",
        use_in_live_call=True
    )

    # 3. Rejected (should NOT return)
    await repo.save_training_note(
        source="s3",
        topic="t3",
        sales_lesson="lesson3",
        good_response_example="example3",
        status="rejected",
        use_in_live_call=False
    )

    # 4. Pending review but somehow use_in_live_call is True (should return)
    note_id_live_flag = await repo.save_training_note(
        source="s4",
        topic="t4",
        sales_lesson="lesson4",
        good_response_example="example4",
        status="pending_review",
        use_in_live_call=True
    )

    # Fetch approved lessons
    lessons = await get_approved_lessons(repo)
    assert len(lessons) == 2

    lesson_ids = [l["id"] for l in lessons]
    assert note_id_approved in lesson_ids
    assert note_id_live_flag in lesson_ids
