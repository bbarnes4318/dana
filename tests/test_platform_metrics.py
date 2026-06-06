"""Unit tests for platform metrics rollups."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from analytics.platform_metrics import get_platform_overview


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_get_platform_overview(repo: Repository):
    # Setup mock calls
    # Call 1: Connected, transferred, qualified
    await repo.save_call(
        call_id="call-1",
        started_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        answered_at=datetime(2026, 6, 1, 12, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 6, 1, 12, 3, tzinfo=timezone.utc),
        duration_seconds=120.0,
        outcome="transferred",
        qualification={
            "open_to_review": True,
            "age_range_confirmed": True,
            "living_independently": True,
            "financial_decision_maker": True,
            "transfer_consent_confirmed": True,
        },
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    )
    
    # Call 2: Connected, wrong number (indicated in transcript)
    await repo.save_call(
        call_id="call-2",
        started_at=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc),
        answered_at=datetime(2026, 6, 1, 13, 0, 30, tzinfo=timezone.utc),
        ended_at=datetime(2026, 6, 1, 13, 1, tzinfo=timezone.utc),
        duration_seconds=60.0,
        outcome="ended",
        transcript=[
            {"speaker": "agent", "text": "Hello, is this Bob?"},
            {"speaker": "prospect", "text": "No, wrong person, you have the wrong number."}
        ],
        created_at=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    )
    
    # Call 3: Connected, wrong number (indicated by outcome)
    await repo.save_call(
        call_id="call-3",
        started_at=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
        answered_at=datetime(2026, 6, 2, 12, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 6, 2, 12, 2, tzinfo=timezone.utc),
        duration_seconds=60.0,
        outcome="wrong_number",
        created_at=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    )

    # Call 4: Callback
    await repo.save_call(
        call_id="call-4",
        started_at=datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc),
        answered_at=datetime(2026, 6, 3, 12, 1, tzinfo=timezone.utc),
        duration_seconds=60.0,
        outcome="callback",
        created_at=datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
    )

    # Call 5: DNC
    await repo.save_call(
        call_id="call-5",
        started_at=datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc),
        answered_at=datetime(2026, 6, 4, 12, 1, tzinfo=timezone.utc),
        duration_seconds=60.0,
        outcome="dnc",
        created_at=datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    )
    
    # Save Call costs (Total = 2.0 + 1.5 + 1.0 = 4.5)
    await repo.save_call_cost(call_id="call-1", component="telephony", estimated_cost=2.0, created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc))
    await repo.save_call_cost(call_id="call-1", component="llm", estimated_cost=1.5, created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc))
    await repo.save_call_cost(call_id="call-2", component="stt", estimated_cost=1.0, created_at=datetime(2026, 6, 1, 13, 5, tzinfo=timezone.utc))
    
    # Test all-time overview
    overview = await get_platform_overview(repo)
    assert overview["total_calls"] == 5
    assert overview["connected_calls"] == 5
    assert overview["transfers"] == 1
    assert overview["callbacks"] == 1
    assert overview["dnc_count"] == 1
    assert overview["wrong_number_count"] == 2  # call-2 and call-3
    assert overview["average_call_duration"] == 72.0  # (120+60+60+60+60)/5 = 72
    
    # Cost per connected minute: Total cost 4.5 / 6 connected minutes = 0.75
    assert overview["cost_per_connected_minute"] == 0.75
    assert overview["cost_per_transfer"] == 4.5
    assert overview["cost_per_qualified_transfer"] == 4.5

    # Test filtered overview (June 1 only)
    overview_filtered = await get_platform_overview(
        repo,
        from_date=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        to_date=datetime(2026, 6, 1, 23, 59, tzinfo=timezone.utc)
    )
    assert overview_filtered["total_calls"] == 2
    assert overview_filtered["connected_calls"] == 2
    assert overview_filtered["transfers"] == 1
    assert overview_filtered["wrong_number_count"] == 1
    assert overview_filtered["cost_per_connected_minute"] == 1.5  # 4.5 cost / 3 minutes = 1.5
