import os
import sys
import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

# Add repo root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from storage.repository import Repository
from telephony.live_campaign_monitor import get_live_campaign_monitor_snapshot, mask_phone


@pytest.fixture
def repo(tmp_path):
    return Repository(data_dir=tmp_path)


def test_mask_phone():
    assert mask_phone("+15551112222") == "+1555111****"
    assert mask_phone("+15055202898") == "+1505520****"
    assert mask_phone("123") == "****"
    assert mask_phone("") == ""


@pytest.mark.asyncio
async def test_monitor_lists_active_calls(repo):
    """21. Verify monitor correctly lists active call placements."""
    # Create active campaign
    await repo.save_outbound_campaign(
        id="campaign:camp-active-1",
        name="Active Campaign",
        status="running",
        max_concurrent_calls=1,
        daily_call_cap=5
    )

    # Create active call attempt
    await repo.save_call_attempt(
        id="attempt-active-1",
        campaign_id="camp-active-1",
        lead_id="lead-1",
        status="in_progress",
        phone_number_redacted="+1555111****",
        caller_id="+15550009999"
    )

    with patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}), \
         patch("telephony.did_pool.DIDPoolManager.list_numbers", return_value=["+15550009999"]):
        snapshot = await get_live_campaign_monitor_snapshot(repo)
        
        assert len(snapshot["active_campaigns"]) == 1
        assert len(snapshot["active_calls"]) == 1
        assert snapshot["active_calls"][0]["call_attempt_id"] == "attempt-active-1"
        assert snapshot["active_calls"][0]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_monitor_lists_recent_exports(repo):
    """22. Verify monitor correctly lists recent post-call exports."""
    await repo.save_call_attempt(
        id="attempt-export-1",
        campaign_id="camp-1",
        lead_id="lead-1",
        status="completed",
        phone_number_redacted="+1555222****",
        post_call_export_path="/data/exports/attempt-export-1.json"
    )

    with patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}), \
         patch("telephony.did_pool.DIDPoolManager.list_numbers", return_value=["+15550009999"]):
        snapshot = await get_live_campaign_monitor_snapshot(repo)
        
        assert len(snapshot["recent_exports"]) == 1
        assert snapshot["recent_exports"][0]["call_attempt_id"] == "attempt-export-1"
        assert snapshot["recent_exports"][0]["post_call_export_path"] == "/data/exports/attempt-export-1.json"


@pytest.mark.asyncio
async def test_monitor_reports_did_usage(repo):
    """23. Verify monitor detects and reports DID caller ID rotation usage."""
    await repo.save_call_attempt(
        id="attempt-did-1",
        campaign_id="camp-1",
        lead_id="lead-1",
        status="in_progress",
        phone_number_redacted="+1555333****",
        caller_id="+15055202898",
        metadata={"selected_caller_id": "+15055202898"}
    )

    with patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}), \
         patch("telephony.did_pool.DIDPoolManager.list_numbers", return_value=["+15055202898"]):
        snapshot = await get_live_campaign_monitor_snapshot(repo)
        
        assert len(snapshot["did_usage"]) == 1
        assert snapshot["did_usage"][0]["caller_id"] == "+1505520****"
        assert snapshot["did_usage"][0]["active_calls"] == 1


@pytest.mark.asyncio
async def test_monitor_masks_phone_numbers(repo):
    """24. Verify monitor strictly redacts and masks all phone numbers."""
    await repo.save_call_attempt(
        id="attempt-mask-1",
        campaign_id="camp-1",
        lead_id="lead-1",
        status="in_progress",
        phone_number_redacted="+15554445555",
        caller_id="+15550001111",
        metadata={"selected_caller_id": "+15550001111"}
    )

    with patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}), \
         patch("telephony.did_pool.DIDPoolManager.list_numbers", return_value=["+15550001111"]):
        snapshot = await get_live_campaign_monitor_snapshot(repo)
        
        active_call = snapshot["active_calls"][0]
        assert active_call["phone_number_masked"] == "+1555444****"
        assert active_call["selected_did"] == "+1555000****"
        assert snapshot["did_usage"][0]["caller_id"] == "+1555000****"
