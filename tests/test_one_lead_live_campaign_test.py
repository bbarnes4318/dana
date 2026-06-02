import os
import sys
import json
import pytest
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from storage.repository import Repository
from telephony.one_lead_live_campaign_test import ControlledCampaignTester, ControlledCampaignTestConfig, ControlledCampaignTestResult
from telephony.live_telephony_readiness import LiveTelephonyReadinessResult
from telephony.livekit_adapter import LiveKitDialResult

@pytest.fixture
def repo(tmp_path):
    return Repository(data_dir=tmp_path)


@pytest.fixture
def mock_readiness():
    res = LiveTelephonyReadinessResult(
        ready=True,
        live_mode_enabled=True,
        required_env={},
        provider_config_ok=True,
        outbound_trunk_id_present=True,
        caller_id_present=True,
        livekit_sdk_available=True,
        agent_worker_ready=True,
        failures=[],
        warnings=[],
        next_steps=[]
    )
    return res


@pytest.fixture
def mock_dial_result():
    return LiveKitDialResult(
        success=True,
        dry_run=False,
        live_mode=True,
        room_name="test-room",
        answered=True,
        livekit_participant_id="part-test-123",
        livekit_sip_call_id="sip-test-123",
        provider_call_id="prov-test-123",
        message="Test dial placed successfully."
    )


@pytest.mark.asyncio
async def test_requires_confirmation(repo):
    """Verify that confirmation 'LIVE CALL' is required for live dialing."""
    tester = ControlledCampaignTester(repository=repo)
    config = ControlledCampaignTestConfig(
        to="+15055202898",
        operator="Jimmy",
        confirm="WRONG CONFIRMATION",
        dry_run=False
    )
    res = await tester.run(config)
    assert res.success is False
    assert "Confirmation 'LIVE CALL' is required" in res.blocker_reason


@pytest.mark.asyncio
async def test_requires_operator(repo):
    """Verify that operator name is required."""
    tester = ControlledCampaignTester(repository=repo)
    config = ControlledCampaignTestConfig(
        to="+15055202898",
        operator="",
        confirm="LIVE CALL",
        dry_run=False
    )
    res = await tester.run(config)
    assert res.success is False
    assert "Operator name/ID is required" in res.blocker_reason


@pytest.mark.asyncio
async def test_requires_allow_now_when_outside_window(repo, mock_readiness):
    """Verify that calling outside the allowed window blocks execution unless allow_now is True."""
    tester = ControlledCampaignTester(repository=repo)
    
    # 2:00 AM New York local time (or default American time) on a Sunday (weekday=6) is outside standard window
    outside_dt = datetime(2026, 6, 7, 2, 0, 0, tzinfo=timezone.utc)
    config = ControlledCampaignTestConfig(
        to="+15055202898",
        operator="Jimmy",
        confirm="LIVE CALL",
        allow_now=False,
        dry_run=True,
        now=outside_dt
    )

    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run", new_callable=AsyncMock) as mock_ready_run, \
         patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}):
        mock_ready_run.return_value = mock_readiness
        res = await tester.run(config)
        assert res.success is False
        assert "Calling window check blocked execution" in res.blocker_reason


@pytest.mark.asyncio
async def test_creates_campaign_with_daily_cap_one(repo, mock_readiness):
    """Verify that the test helper creates/reuses the campaign with daily cap and concurrent calls set to 1."""
    tester = ControlledCampaignTester(repository=repo)
    # 18:00 UTC on a Monday is 12:00 PM Denver time (inside window)
    dt = datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc)
    config = ControlledCampaignTestConfig(
        to="+15055202898",
        operator="Jimmy",
        confirm="LIVE CALL",
        allow_now=False,
        dry_run=True,
        now=dt
    )

    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run", new_callable=AsyncMock) as mock_ready_run, \
         patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}), \
         patch("telephony.livekit_adapter.LiveKitOutboundAdapter.dial", new_callable=AsyncMock) as mock_dial:
        mock_ready_run.return_value = mock_readiness
        res = await tester.run(config)
        assert res.success is True
        
        # Load campaign from DB
        camp = await repo.get_outbound_campaign(res.campaign_id)
        assert camp is not None
        assert camp["name"] == "Dana Live One-Lead Test"
        assert camp["max_concurrent_calls"] == 1
        assert camp["daily_call_cap"] == 1


@pytest.mark.asyncio
async def test_imports_exactly_one_lead(repo, mock_readiness):
    """Verify that lead imports clear existing leads and results in exactly 1 lead in the campaign."""
    tester = ControlledCampaignTester(repository=repo)
    dt = datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc)
    config = ControlledCampaignTestConfig(
        to="+15055202898",
        operator="Jimmy",
        confirm="LIVE CALL",
        allow_now=False,
        dry_run=True,
        now=dt
    )

    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run", new_callable=AsyncMock) as mock_ready_run, \
         patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}):
        mock_ready_run.return_value = mock_readiness
        
        # Populate pre-existing mock leads under this campaign ID first
        campaign_id = "mock-campaign-id"
        # Setup pre-existing campaign
        await repo.save_outbound_campaign(id=campaign_id, name="Dana Live One-Lead Test", status="draft")
        await repo.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001")
        await repo.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550002")

        res = await tester.run(config)
        assert res.success is True
        
        # Query leads from repository
        leads = await repo.query_campaign_leads({"campaign_id": res.campaign_id})
        assert len(leads) == 1
        assert leads[0]["phone_number"] == "+15055202898"


@pytest.mark.asyncio
async def test_runs_dialer_with_max_calls_one(repo, mock_readiness):
    """Verify that the dialer runs with max_calls limit of 1."""
    tester = ControlledCampaignTester(repository=repo)
    dt = datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc)
    config = ControlledCampaignTestConfig(
        to="+15055202898",
        operator="Jimmy",
        confirm="LIVE CALL",
        allow_now=False,
        dry_run=True,
        now=dt
    )

    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run", new_callable=AsyncMock) as mock_ready_run, \
         patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}), \
         patch("telephony.dialer_queue.DialerQueue.run_tick", new_callable=AsyncMock) as mock_tick:
        mock_ready_run.return_value = mock_readiness
        mock_tick.return_value = MagicMock(attempt_ids=[], errors=[], warnings=[], blocked_reason="Test run completed.")
        
        await tester.run(config)
        
        # Check that run_tick was called with max_calls=1
        called_config = mock_tick.call_args[0][0]
        assert called_config.max_calls == 1


@pytest.mark.asyncio
async def test_stops_campaign_after_test(repo, mock_readiness):
    """Verify that campaign status is set to stopped immediately after running the test."""
    tester = ControlledCampaignTester(repository=repo)
    dt = datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc)
    config = ControlledCampaignTestConfig(
        to="+15055202898",
        operator="Jimmy",
        confirm="LIVE CALL",
        allow_now=False,
        dry_run=True,
        now=dt
    )

    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run", new_callable=AsyncMock) as mock_ready_run, \
         patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}):
        mock_ready_run.return_value = mock_readiness
        res = await tester.run(config)
        assert res.success is True
        assert res.campaign_stopped is True
        
        # Check database status
        camp = await repo.get_outbound_campaign(res.campaign_id)
        assert camp["status"] == "stopped"


@pytest.mark.asyncio
async def test_never_dials_more_than_one(repo, mock_readiness, mock_dial_result):
    """Verify that even if capacity exceeds, dialer tick enforces max calls limit of 1."""
    import asyncio
    tester = ControlledCampaignTester(repository=repo)
    dt = datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc)
    config = ControlledCampaignTestConfig(
        to="+15055202898",
        operator="Jimmy",
        confirm="LIVE CALL",
        allow_now=False,
        dry_run=False, # Make it place a call
        now=dt
    )

    original_sleep = asyncio.sleep
    async def mock_sleep(delay):
        attempts = await repo.query_call_attempts({})
        for attempt in attempts:
            if attempt["status"] != "completed":
                attempt["status"] = "completed"
                attempt["outcome"] = "answered"
                await repo.save_call_attempt(**attempt)
        await original_sleep(0.001)

    with patch.dict(os.environ, {
        "TELEPHONY_LIVE_MODE": "true",
        "DANA_CONFIRM_PLACE_CALL": "yes",
        "LIVEKIT_SIP_OUTBOUND_TRUNK_ID": "ST_dummy"
    }):
        with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run", new_callable=AsyncMock) as mock_ready_run, \
             patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}), \
             patch("telephony.livekit_adapter.LiveKitOutboundAdapter.dial", new_callable=AsyncMock) as mock_dial, \
             patch("asyncio.sleep", side_effect=mock_sleep):
            mock_ready_run.return_value = mock_readiness
            mock_dial.return_value = mock_dial_result
            
            await repo.save_did(
                provider="telnyx",
                phone_number="+15055202898",
                status="active",
                source="manual",
                verified_for_provider=True
            )

            res = await tester.run(config)
            print("TESTER RESULT ERRORS:", res.errors)
            print("TESTER RESULT:", res)
            assert res.success is True
            
            # Ensure dial was called exactly once
            assert mock_dial.call_count == 1


@pytest.mark.asyncio
async def test_no_real_call_in_tests(repo, mock_readiness):
    """Safety check: confirm that LiveKitOutboundAdapter.dial is not invoked with real live credentials unless explicitly mocked."""
    tester = ControlledCampaignTester(repository=repo)
    dt = datetime(2026, 6, 1, 18, 0, 0, tzinfo=timezone.utc)
    config = ControlledCampaignTestConfig(
        to="+15055202898",
        operator="Jimmy",
        confirm="LIVE CALL",
        allow_now=False,
        dry_run=True, # Dry run does not dial
        now=dt
    )

    with patch("telephony.live_telephony_readiness.LiveTelephonyReadinessChecker.run", new_callable=AsyncMock) as mock_ready_run, \
         patch("telephony.livekit_agent_worker.check_worker_dependencies", return_value={"ready": True}), \
         patch("telephony.livekit_adapter.LiveKitOutboundAdapter.dial", new_callable=AsyncMock) as mock_dial:
        mock_ready_run.return_value = mock_readiness
        
        res = await tester.run(config)
        assert res.success is True
        
        # Adapter dial should NOT be called in dry run
        mock_dial.assert_not_called()


@pytest.mark.asyncio
async def test_outputs_clean_json(tmp_path):
    """Verify that CLI script runs and outputs valid JSON to stdout."""
    import subprocess
    import sys
    
    # Run CLI script in dry-run mode
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "run_one_lead_live_campaign_test.py"
    
    # Run with empty operators/confirmation to let it print JSON failure
    cmd = [sys.executable, str(script_path), "--to", "+15055202898", "--operator", "", "--confirm", "LIVE CALL", "--dry-run", "--output-dir", str(tmp_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # Check stdout is valid JSON
    try:
        data = json.loads(result.stdout)
        assert data["success"] is False
        assert any("Operator name/ID is required" in err for err in data["errors"])
    except json.JSONDecodeError:
        pytest.fail(f"CLI stdout was not valid JSON. Stdout: {result.stdout}\nStderr: {result.stderr}")
