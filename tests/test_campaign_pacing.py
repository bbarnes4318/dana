"""Tests for CampaignPacer and CampaignRunner integration with pacing checks."""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from unittest import mock
import pytest

from storage.repository import Repository
from dialer.campaign_runner import CampaignRunner
from dialer.pacing import CampaignPacer
from runtime.hot_state import InMemoryHotStateStore


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def hot_store():
    """Return an InMemoryHotStateStore."""
    return InMemoryHotStateStore(key_prefix="test")


@pytest.fixture
def pacer(repo, hot_store):
    """Return a CampaignPacer backed by InMemoryHotStateStore."""
    return CampaignPacer(repo, hot_store)


@pytest.mark.asyncio
async def test_campaign_pacer_limits(repo, pacer):
    """Verify pacer correctly enforces concurrent calls and cpm limits."""
    campaign_id = "pacing-test"
    campaign_data = {
        "id": f"campaign:{campaign_id}",
        "campaign_id": campaign_id,
        "name": "Pacing Test Campaign",
        "status": "active",
        "is_paused": False,
        "max_concurrent_calls": 2,
        "calls_per_minute": 5
    }
    await repo.save_campaign(**campaign_data)

    # 1. Pacing should allow starting a call initially
    assert await pacer.can_start_call(campaign_id) is True

    # 2. Start two calls (max_concurrent = 2)
    await pacer.mark_call_started(campaign_id, "call-1")
    await pacer.mark_call_started(campaign_id, "call-2")
    
    # 3. Pacing should now block because concurrent calls reached max_concurrent
    assert await pacer.can_start_call(campaign_id) is False

    # 4. Finish a call
    await pacer.mark_call_finished(campaign_id, "call-1")
    
    # 5. Pacing should allow starting a call again
    assert await pacer.can_start_call(campaign_id) is True

    # 6. Mark three more calls started to test CPM limit (total 5 started in last minute)
    await pacer.mark_call_started(campaign_id, "call-3")
    await pacer.mark_call_started(campaign_id, "call-4")
    await pacer.mark_call_started(campaign_id, "call-5")
    
    # 7. Should block even if active calls < max_concurrent because CPM reached
    await pacer.mark_call_finished(campaign_id, "call-2")
    await pacer.mark_call_finished(campaign_id, "call-3")
    assert len(await pacer.get_active_calls(campaign_id)) == 2  # call-4, call-5
    
    # Calls started = call-1,2,3,4,5 (5 calls in rolling minute) -> CPM limit is 5
    assert await pacer.can_start_call(campaign_id) is False


@pytest.mark.asyncio
async def test_campaign_runner_respects_pacing_without_incrementing_attempts(repo, hot_store, tmp_path):
    """Verify CampaignRunner checks pacing, blocks before locking, and doesn't increment attempts."""
    campaign_id = "runner-pacing-test"
    campaign_data = {
        "id": f"campaign:{campaign_id}",
        "campaign_id": campaign_id,
        "name": "Pacing Test Campaign",
        "status": "active",
        "is_paused": False,
        "max_concurrent_calls": 1,
        "calls_per_minute": 5
    }
    await repo.save_campaign(**campaign_data)

    lead_id = "lead-pacing-1"
    lead_data = {
        "id": lead_id,
        "lead_id": lead_id,
        "phone_e164": "+13055550199",
        "campaign_id": campaign_id,
        "status": "pending",
        "attempts": 0,
        "priority": 0
    }
    await repo.save_lead(lead_data)
    
    # Set up active caller ID
    await repo.save_caller_id(caller_id="+18005550199", campaign_id=campaign_id, status="active")

    # Set up CampaignRunner with our InMemoryHotStateStore
    runner = CampaignRunner(repository=repo)
    runner.campaign_pacer = CampaignPacer(repo, hot_store)

    # 1. Fill pacing to block dialing
    await runner.campaign_pacer.mark_call_started(campaign_id, "pre-existing-call")
    
    # 2. Run dialing once
    now_utc = datetime.now(timezone.utc)
    status = await runner.run_once(campaign_id=campaign_id, now=now_utc)
    
    # 3. Pacing check should block execution
    assert status == "pacing_blocked"

    # 4. Verify lead attempts has not incremented and status remains pending
    updated_lead = await repo.get_lead(lead_id)
    assert updated_lead is not None
    assert updated_lead["attempts"] == 0
    assert updated_lead["status"] == "pending"
    assert updated_lead.get("lock_holder_id") is None
