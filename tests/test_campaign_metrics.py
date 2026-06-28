"""Unit tests for campaign metrics rollups."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from storage.repository import Repository
from analytics.campaign_metrics import get_campaign_analytics


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_get_campaign_analytics(repo: Repository):
    # Setup mock caller IDs
    # Save directly to store to avoid validation / connection rules in save_caller_id
    await repo.store.save("dids", {
        "id": "cid-1",
        "phone_number": "+12345",
        "provider": "telnyx",
        "stir_shaken_attestation": "A",
        "status": "active",
        "source": "manual"
    })
    await repo.store.save("dids", {
        "id": "cid-2",
        "phone_number": "+54321",
        "provider": "telnyx",
        "stir_shaken_attestation": "B",
        "status": "active",
        "source": "manual"
    })
    
    # Setup mock calls
    # Call 1: campaign-1, connected, transferred, caller_id = +12345
    await repo.save_call(
        call_id="call-1",
        campaign_id="campaign-1",
        caller_id="+12345",
        answered_at=datetime(2026, 6, 1, 12, 1, tzinfo=timezone.utc),
        duration_seconds=120.0,
        outcome="transferred",
        created_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    )
    # Call 2: campaign-1, not connected, outcome = dnc, caller_id = +12345
    await repo.save_call(
        call_id="call-2",
        campaign_id="campaign-1",
        caller_id="+12345",
        answered_at=None,
        duration_seconds=0.0,
        outcome="dnc",
        created_at=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    )
    # Call 3: campaign-2 (other campaign), connected, caller_id = +54321
    await repo.save_call(
        call_id="call-3",
        campaign_id="campaign-2",
        caller_id="+54321",
        answered_at=datetime(2026, 6, 1, 14, 1, tzinfo=timezone.utc),
        duration_seconds=60.0,
        outcome="ended",
        created_at=datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
    )
    # Call 4: campaign-1, old call (outside date range)
    await repo.save_call(
        call_id="call-old",
        campaign_id="campaign-1",
        caller_id="+12345",
        answered_at=datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
        duration_seconds=60.0,
        outcome="ended",
        created_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    )
    
    # Costs
    # Call 1 cost = 2.0
    await repo.save_call_cost(call_id="call-1", campaign_id="campaign-1", component="telephony", estimated_cost=2.0, created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc))
    # Call 2 cost = 0.5
    await repo.save_call_cost(call_id="call-2", campaign_id="campaign-1", component="telephony", estimated_cost=0.5, created_at=datetime(2026, 6, 1, 13, 5, tzinfo=timezone.utc))
    # Call 3 cost = 1.0 (campaign-2)
    await repo.save_call_cost(call_id="call-3", campaign_id="campaign-2", component="telephony", estimated_cost=1.0, created_at=datetime(2026, 6, 1, 14, 5, tzinfo=timezone.utc))

    # Test campaign-1 all-time metrics
    metrics = await get_campaign_analytics(repo, campaign_id="campaign-1")
    # Total calls for campaign-1 = 3 (call-1, call-2, call-old)
    # Answered = 2 (call-1, call-old). Answer rate = 2/3 = 0.6667
    # Transfers = 1. Transfer rate = 1/3 = 0.3333
    # DNC = 1. DNC rate = 1/3 = 0.3333
    # Callbacks = 0.
    assert metrics["total_calls"] == 3
    assert metrics["answer_rate"] == 0.6667
    assert metrics["transfer_rate"] == 0.3333
    assert metrics["dnc_rate"] == 0.3333
    assert metrics["callback_rate"] == 0.0
    
    # Cost per outcome for campaign-1
    # call-1 cost = 2.0 (outcome: transferred)
    # call-2 cost = 0.5 (outcome: dnc)
    # call-old cost = 0.0 (no cost record) (outcome: ended)
    assert metrics["cost_per_outcome"]["transferred"] == 2.0
    assert metrics["cost_per_outcome"]["dnc"] == 0.5
    assert metrics["cost_per_outcome"]["ended"] == 0.0
    
    # Caller ID performance for campaign-1 (calls: call-1, call-2, call-old)
    # Number +12345 total = 3, answered = 2, dnc = 1, stir attestation = A
    cid_stats = metrics["caller_id_performance"]["+12345"]
    assert cid_stats["total_calls"] == 3
    assert cid_stats["answer_rate"] == 0.6667
    assert cid_stats["dnc_rate"] == 0.3333
    assert cid_stats["stir_shaken_status"] == "A"

    # Test campaign-1 filtered metrics (June 1 only)
    metrics_filtered = await get_campaign_analytics(
        repo,
        campaign_id="campaign-1",
        from_date=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        to_date=datetime(2026, 6, 1, 23, 59, tzinfo=timezone.utc)
    )
    # call-old is excluded
    # Total calls = 2 (call-1, call-2)
    # Answered = 1 (call-1). Answer rate = 1/2 = 0.5
    # Transfers = 1. Transfer rate = 1/2 = 0.5
    # DNC = 1. DNC rate = 1/2 = 0.5
    assert metrics_filtered["total_calls"] == 2
    assert metrics_filtered["answer_rate"] == 0.5
    assert metrics_filtered["transfer_rate"] == 0.5
    assert metrics_filtered["dnc_rate"] == 0.5
    
    cid_stats_filtered = metrics_filtered["caller_id_performance"]["+12345"]
    assert cid_stats_filtered["total_calls"] == 2
    assert cid_stats_filtered["answer_rate"] == 0.5
    assert cid_stats_filtered["dnc_rate"] == 0.5
