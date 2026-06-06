"""Unit tests for cost metrics rollups."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from analytics.cost_rollups import get_cost_metrics


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_get_cost_metrics(repo: Repository):
    # Setup mock calls
    # Call 1: connected, 120s duration
    await repo.save_call(
        call_id="call-1",
        answered_at=datetime(2026, 6, 1, 12, 1, tzinfo=timezone.utc),
        duration_seconds=120.0,
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    )
    # Call 2: not connected
    await repo.save_call(
        call_id="call-2",
        answered_at=None,
        duration_seconds=0.0,
        created_at=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    )
    
    # Costs
    # Call 1 costs: Telephony=1.0, LLM=2.0, STT=0.5, TTS=0.5
    # Campaign: campaign-A
    await repo.save_call_cost(call_id="call-1", campaign_id="campaign-A", component="telephony", estimated_cost=1.0, created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc))
    await repo.save_call_cost(call_id="call-1", campaign_id="campaign-A", component="llm", estimated_cost=2.0, created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc))
    await repo.save_call_cost(call_id="call-1", campaign_id="campaign-A", component="stt", estimated_cost=0.5, created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc))
    await repo.save_call_cost(call_id="call-1", campaign_id="campaign-A", component="tts", estimated_cost=0.5, created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc))
    
    # Call 2 costs: Telephony=0.5
    # Campaign: campaign-B
    await repo.save_call_cost(call_id="call-2", campaign_id="campaign-B", component="telephony", estimated_cost=0.5, created_at=datetime(2026, 6, 1, 13, 5, tzinfo=timezone.utc))
    
    # Cost outside date range
    await repo.save_call_cost(call_id="call-old", campaign_id="campaign-A", component="telephony", estimated_cost=5.0, created_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc))

    # Test all-time costs
    metrics = await get_cost_metrics(repo)
    # Total cost = 1.0 + 2.0 + 0.5 + 0.5 + 0.5 + 5.0 = 9.5
    # Total calls in database = 2 + (we did not save a call record for call-old, but let's see. If call_costs has call-old but calls doesn't, total_calls is 2).
    # Avg cost per call = 9.5 / 2 = 4.75
    # Connected minutes = 120s / 60 = 2.0 minutes.
    # Avg cost per connected minute = 9.5 / 2.0 = 4.75
    assert metrics["total_cost"] == 9.5
    assert metrics["average_cost_per_call"] == 4.75
    assert metrics["average_cost_per_connected_minute"] == 4.75
    assert metrics["component_costs"]["telephony"] == 6.5
    assert metrics["campaign_costs"]["campaign-A"] == 9.0
    assert metrics["campaign_costs"]["campaign-B"] == 0.5

    # Test filtered costs (June 1 only)
    metrics_filtered = await get_cost_metrics(
        repo,
        from_date=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        to_date=datetime(2026, 6, 1, 23, 59, tzinfo=timezone.utc)
    )
    # Total cost = 1.0 + 2.0 + 0.5 + 0.5 + 0.5 = 4.5
    # Total calls = 2
    # Avg cost per call = 4.5 / 2 = 2.25
    # Connected minutes = 2.0
    # Avg cost per connected minute = 4.5 / 2.0 = 2.25
    assert metrics_filtered["total_cost"] == 4.5
    assert metrics_filtered["average_cost_per_call"] == 2.25
    assert metrics_filtered["average_cost_per_connected_minute"] == 2.25
    assert metrics_filtered["component_costs"]["telephony"] == 1.5
    assert metrics_filtered["component_costs"]["llm"] == 2.0
    assert metrics_filtered["component_costs"]["stt"] == 0.5
    assert metrics_filtered["component_costs"]["tts"] == 0.5
    assert metrics_filtered["campaign_costs"]["campaign-A"] == 4.0
    assert metrics_filtered["campaign_costs"]["campaign-B"] == 0.5
