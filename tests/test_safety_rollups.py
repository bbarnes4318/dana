"""Unit tests for safety metrics rollups."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from analytics.safety_rollups import get_safety_metrics


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_get_safety_metrics(repo: Repository):
    # Setup mock calls
    # Call 1: Has issues: transfer_before_consent, agent_price_quote (compliance hard fail, consent violation)
    await repo.save_call(
        call_id="call-1",
        compliance_flags={"issues": ["transfer_before_consent", "agent_price_quote"]},
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    )
    
    # Call 2: Has issues: continued_talking_after_dnc (compliance hard fail, DNC failure)
    await repo.save_call(
        call_id="call-2",
        compliance_flags={"issues": ["continued_talking_after_dnc"]},
        created_at=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    )
    
    # Call 3: No issues, but wrong number outcome
    await repo.save_call(
        call_id="call-3",
        compliance_flags={"issues": []},
        outcome="wrong_number",
        created_at=datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
    )
    
    # Call 4: Old call with issues
    await repo.save_call(
        call_id="call-old",
        compliance_flags={"issues": ["agent_price_quote"]},
        created_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    )
    
    # Setup mock turns (unsafe phrase blocks)
    # Turn for Call 1: 2 warnings
    await repo.save_call_turn(
        call_id="call-1",
        turn_number=1,
        speaker="agent",
        text="Hello.",
        stage="opening",
        compliance_warnings=["warning 1", "warning 2"]
    )
    # Turn for Call 2: 1 warning
    await repo.save_call_turn(
        call_id="call-2",
        turn_number=1,
        speaker="agent",
        text="Hello.",
        stage="opening",
        compliance_warnings=["warning 3"]
    )
    # Turn for Call 3: 0 warnings
    await repo.save_call_turn(
        call_id="call-3",
        turn_number=1,
        speaker="agent",
        text="Hello.",
        stage="opening",
        compliance_warnings=[]
    )
    # Turn for Call Old: 1 warning
    await repo.save_call_turn(
        call_id="call-old",
        turn_number=1,
        speaker="agent",
        text="Hello.",
        stage="opening",
        compliance_warnings=["old warning"]
    )

    # Test all-time safety metrics
    metrics = await get_safety_metrics(repo)
    assert metrics["compliance_hard_fails"] == 3  # call-1, call-2, call-old
    assert metrics["dnc_failures"] == 1           # call-2
    assert metrics["transfer_consent_violations"] == 1 # call-1
    assert metrics["wrong_number_failures"] == 1  # call-3
    assert metrics["unsafe_phrase_blocks"] == 4   # 2 (call-1) + 1 (call-2) + 1 (call-old)

    # Test date filtered safety metrics (June 1 only)
    metrics_filtered = await get_safety_metrics(
        repo,
        from_date=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        to_date=datetime(2026, 6, 1, 23, 59, tzinfo=timezone.utc)
    )
    # call-old is excluded
    assert metrics_filtered["compliance_hard_fails"] == 2  # call-1, call-2
    assert metrics_filtered["dnc_failures"] == 1
    assert metrics_filtered["transfer_consent_violations"] == 1
    assert metrics_filtered["wrong_number_failures"] == 1  # call-3
    assert metrics_filtered["unsafe_phrase_blocks"] == 3   # 2 (call-1) + 1 (call-2)

