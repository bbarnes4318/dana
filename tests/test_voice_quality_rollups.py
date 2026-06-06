"""Unit tests for voice quality and humanlikeness rollups."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from analytics.voice_quality_rollups import get_voice_quality_metrics


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_get_voice_quality_metrics(repo: Repository):
    # Setup mock calls
    await repo.save_call(
        call_id="call-1",
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    )
    await repo.save_call(
        call_id="call-2",
        created_at=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    )
    await repo.save_call(
        call_id="call-old",
        created_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    )
    
    # Setup mock QA reports
    # Call 1: Bot-likeness = 8.0, Realism = 9.0
    await repo.save_qa_report(
        call_id="call-1",
        scores={"bot_likeness": 8.0, "human_realism": 9.0},
        created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc)
    )
    # Call 2: Bot-likeness = 6.0, Realism = 7.0
    await repo.save_qa_report(
        call_id="call-2",
        scores={"bot_likeness": 6.0, "human_realism": 7.0},
        created_at=datetime(2026, 6, 1, 13, 5, tzinfo=timezone.utc)
    )
    # Call Old: Bot-likeness = 10.0, Realism = 10.0
    await repo.save_qa_report(
        call_id="call-old",
        scores={"bot_likeness": 10.0, "human_realism": 10.0},
        created_at=datetime(2026, 5, 1, 12, 5, tzinfo=timezone.utc)
    )
    
    # Setup call turns
    # Call 1 turns: 2 agent turns.
    # Turn 1: 5 words. No repetition.
    await repo.save_call_turn(
        call_id="call-1",
        turn_number=1,
        speaker="agent",
        text="Hello, how are you today?",
        stage="opening",
        interrupted=False
    )
    # Turn 2: 15 words. Overuses "perfect" (spoken 3 times -> count=3, limit=2 -> overuse=1 -> 2.0 deduction). Repetition score = 10.0 - 2.0 = 8.0
    await repo.save_call_turn(
        call_id="call-1",
        turn_number=2,
        speaker="agent",
        text="Perfect. I understand. Perfect. Got that. Perfect. Let me verify all of these details now.",
        stage="interest_check",
        interrupted=True  # Missing repair language -> 3.0 deduction. Interruption repair score = 7.0
    )
    
    # Call 2 turns: 1 agent turn.
    # Turn 1: 10 words. Interrupted, with repair language -> 0.0 deduction. Interruption repair score = 10.0
    await repo.save_call_turn(
        call_id="call-2",
        turn_number=1,
        speaker="agent",
        text="Sorry, go ahead. I am checking if you are open.",
        stage="opening",
        interrupted=True
    )

    # Test all-time metrics
    metrics = await get_voice_quality_metrics(repo)
    # Avg Bot-likeness: (8.0 + 6.0 + 10.0) / 3 = 8.0
    # Avg Realism: (9.0 + 7.0 + 10.0) / 3 = 8.67
    # Avg Repetition: (8.0 [call-1] + 10.0 [call-2] + 10.0 [call-old]) / 3 = 9.33
    # Avg Words per turn: (5 + 15 + 10) / 3 = 10.0
    # Avg Interruption Repair: (7.0 [call-1] + 10.0 [call-2] + 10.0 [call-old]) / 3 = 9.0
    assert metrics["bot_likeness_score"] == 8.0
    assert metrics["human_realism_score"] == 8.67
    assert metrics["repetition_score"] == 9.33
    assert metrics["average_words_per_turn"] == 10.0
    assert metrics["interruption_repair_score"] == 9.0

    # Test filtered metrics (June 1 only)
    metrics_filtered = await get_voice_quality_metrics(
        repo,
        from_date=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        to_date=datetime(2026, 6, 1, 23, 59, tzinfo=timezone.utc)
    )
    # Avg Bot-likeness: (8.0 + 6.0) / 2 = 7.0
    # Avg Realism: (9.0 + 7.0) / 2 = 8.0
    # Avg Repetition: (8.0 + 10.0) / 2 = 9.0
    # Avg Words per turn: (5 + 15 + 10) / 3 = 10.0 (since date filter filters calls, and we only process turns for filtered calls - call-1 and call-2 have 3 turns in total, so it's still 10.0)
    # Avg Interruption Repair: (7.0 + 10.0) / 2 = 8.5
    assert metrics_filtered["bot_likeness_score"] == 7.0
    assert metrics_filtered["human_realism_score"] == 8.0
    assert metrics_filtered["repetition_score"] == 9.0
    assert metrics_filtered["average_words_per_turn"] == 10.0
    assert metrics_filtered["interruption_repair_score"] == 8.5
