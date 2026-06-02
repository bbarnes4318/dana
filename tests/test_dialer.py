"""Tests for the outbound dialer and campaign runner.

Verifies pacing, retry rules, caller ID rotation, compliance gating,
attempts counting, and dry-run safety behavior.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from unittest import mock
import pytest

from storage.repository import Repository
from dialer.campaign_runner import CampaignRunner
from dialer.lead_queue import LeadQueue
from dialer.caller_id_pool import CallerIdPool
from dialer.retry_policy import RetryPolicy
from dialer.call_service import CallService
from compliance.consent_record import ConsentRecord
from compliance.dnc_registry import DatabaseDNCRegistry
from telephony.agent_availability import InMemoryAgentAvailabilityStore, LicensedAgent


@pytest.fixture
def repo(tmp_path):
    """Return a Repository backed by a tmp_path JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.fixture(autouse=True)
def clean_hot_state():
    """Reset the hot state store singleton to prevent test state leakage."""
    from runtime import hot_state
    hot_state._store_instance = None
    hot_state._degraded_mode = False
    yield


@pytest.fixture
def agent_store():
    """Return an InMemoryAgentAvailabilityStore."""
    return InMemoryAgentAvailabilityStore()


@pytest.fixture
def default_campaign(repo):
    """Return a helper to create campaign data."""
    async def _create(campaign_id="camp-test", **kwargs):
        data = {
            "id": f"campaign:{campaign_id}",
            "campaign_id": campaign_id,
            "name": "Test Campaign",
            "status": "active",
            "is_paused": False,
            "approved_consent_sources": ["trustedform", "landing_page"],
            "max_attempts": 3,
            "allowed_calling_hours": (8, 20),
            "caller_id": "+18005550199",
            "active_caller_ids": ["+18005550199"],
            "require_live_transfer": False,
            "target_states": ["FL", "TX"]
        }
        data.update(kwargs)
        await repo.save_campaign(**data)
        return data
    return _create


@pytest.fixture
def default_lead(repo):
    """Return a helper to create lead data."""
    async def _create(lead_id="lead-123", campaign_id="camp-test", phone="+13055550199", state="FL", **kwargs):
        data = {
            "id": lead_id,
            "lead_id": lead_id,
            "phone_e164": phone,
            "campaign_id": campaign_id,
            "status": "pending",
            "lead_state": state,
            "attempts": 0,
            "priority": 0
        }
        data.update(kwargs)
        
        # Save both direct properties and lead_profile wrapper
        data["lead_profile"] = {
            "lead_id": lead_id,
            "lead_phone_e164": phone,
            "campaign_id": campaign_id,
            "status": data["status"],
            "consent_artifact_id": data.get("consent_artifact_id", "art-123"),
            "consent_source": "trustedform"
        }
        await repo.save_lead(data)
        return data
    return _create


@pytest.fixture
def default_consent(repo):
    """Return a helper to create consent record."""
    async def _create(lead_id="lead-123", phone="+13055550199", campaign_id="camp-test", **kwargs):
        data = {
            "id": f"consent:{lead_id}",
            "consent_artifact_id": "art-123",
            "lead_id": lead_id,
            "phone_e164": phone,
            "source_vendor": "trustedform",
            "consent_text": "Agree to call",
            "consent_timestamp": "2026-05-27T12:00:00Z",
            "campaign_id": campaign_id
        }
        data.update(kwargs)
        await repo.save_consent_record(**data)
        return data
    return _create


@pytest.mark.asyncio
async def test_outside_calling_window_releases_lead_no_attempts_increment(
    repo, default_campaign, default_lead, default_consent
):
    """Test that outside calling window compliance block releases lead without incrementing attempts."""
    await default_campaign(campaign_id="camp-test")
    lead = await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    runner = CampaignRunner(repository=repo)

    # 2:00 AM UTC in winter is 9:00 PM local time in Florida (Eastern Time).
    # Since allowed hours are (8, 20), this is outside calling window.
    now_utc = datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc)

    status = await runner.run_once(campaign_id="camp-test", now=now_utc)
    assert status == "compliance_blocked_outside_calling_window"

    # Verify lead lock was released, status is pending, and attempts is 0
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["lock_holder_id"] is None
    assert updated_lead["locked_at"] is None
    assert updated_lead["status"] == "pending"
    assert updated_lead["attempts"] == 0


@pytest.mark.asyncio
async def test_missing_consent_releases_lead_no_attempts_increment(
    repo, default_campaign, default_lead
):
    """Test that missing consent record blocks dialing and releases lead without incrementing attempts."""
    await default_campaign(campaign_id="camp-test")
    # Lead exists but NO consent record is created
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc) # 10:00 AM local (Eastern)

    status = await runner.run_once(campaign_id="camp-test", now=now_utc)
    assert status == "compliance_blocked_missing_consent_record"

    # Verify status is consent_missing, lock is released, and attempts is 0
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["lock_holder_id"] is None
    assert updated_lead["status"] == "consent_missing"
    assert updated_lead["attempts"] == 0


@pytest.mark.asyncio
async def test_dnc_block_releases_lead_no_attempts_increment(
    repo, default_campaign, default_lead, default_consent
):
    """Test that lead matching DNC list is blocked from dialing and attempts are not incremented."""
    await default_campaign(campaign_id="camp-test")
    await default_lead(lead_id="lead-123", campaign_id="camp-test", phone="+13055550199", state="FL")
    await default_consent(lead_id="lead-123", phone="+13055550199", campaign_id="camp-test")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    # Add lead's phone to DNC registry
    await repo.save_dnc_request(
        call_id="call-000",
        lead_id="lead-123",
        phone_e164="+13055550199",
        campaign_id="camp-test",
        reason="DNC request"
    )

    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    status = await runner.run_once(campaign_id="camp-test", now=now_utc)
    assert status == "compliance_blocked_dnc"

    # Verify status is 'dnc', lock is released, and attempts is 0
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["lock_holder_id"] is None
    assert updated_lead["status"] == "dnc"
    assert updated_lead["attempts"] == 0


@pytest.mark.asyncio
async def test_inactive_caller_id_releases_lead_no_attempts_increment(
    repo, default_campaign, default_lead, default_consent
):
    """Test that inactive caller ID releases lead lock without incrementing attempts."""
    # Create campaign with a caller ID that is NOT in active_caller_ids
    await default_campaign(campaign_id="camp-test", caller_id="+18005550199", active_caller_ids=["+18881234567"])
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")

    # Add caller ID to database
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="inactive")

    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    status = await runner.run_once(campaign_id="camp-test", now=now_utc)
    assert status == "compliance_blocked_caller_id_inactive"

    # Verify status is pending, lock is released, and attempts is 0
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["lock_holder_id"] is None
    assert updated_lead["status"] == "pending"
    assert updated_lead["attempts"] == 0


@pytest.mark.asyncio
async def test_attempts_increment_only_after_dry_run_or_outbound_starts(
    repo, default_campaign, default_lead, default_consent
):
    """Test that attempts are incremented only after compliance checks pass and call start begins."""
    await default_campaign(campaign_id="camp-test")
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")

    # Save active caller ID
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    with mock.patch.dict(os.environ, {"DANA_CONFIRM_PLACE_CALL": "no"}):
        status = await runner.run_once(campaign_id="camp-test", now=now_utc)
        assert status == "success_human_answered"

    # Verify attempts incremented to 1
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["attempts"] == 1
    assert updated_lead["last_attempt_at"] is not None


@pytest.mark.asyncio
async def test_voicemail_does_not_start_dana_voice_flow(
    repo, default_campaign, default_lead, default_consent
):
    """Test that AMD voicemail detection updates disposition and releases lead without starting AgentSession/Dana voice flow."""
    await default_campaign(campaign_id="camp-test", voicemail_retry_allowed=True)
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    with mock.patch.dict(os.environ, {"DANA_CONFIRM_PLACE_CALL": "no"}):
        # Pass voicemail simulated outcome
        status = await runner.run_once(campaign_id="camp-test", now=now_utc, simulated_outcome="voicemail")
        assert status == "retryable_failure_voicemail"

    # Verify lead status is failed and lock is released
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["lock_holder_id"] is None
    assert updated_lead["status"] == "failed"
    assert updated_lead["attempts"] == 1
    assert updated_lead["retry_after"] is not None


@pytest.mark.asyncio
async def test_human_answered_proceeds_to_dana_flow(
    repo, default_campaign, default_lead, default_consent
):
    """Test that human_answered proceeds to Dana voice flow and completes lead."""
    await default_campaign(campaign_id="camp-test")
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    with mock.patch.dict(os.environ, {"DANA_CONFIRM_PLACE_CALL": "no"}):
        status = await runner.run_once(campaign_id="camp-test", now=now_utc, simulated_outcome="human_answered")
        assert status == "success_human_answered"

    # Verify lead status is completed
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["status"] == "completed"


@pytest.mark.asyncio
async def test_no_agent_available_does_not_burn_attempt(
    repo, default_campaign, default_lead, default_consent, agent_store
):
    """Test that if campaign requires live transfer and no agents are available, lead lock is released without incrementing attempts."""
    await default_campaign(campaign_id="camp-test", require_live_transfer=True)
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")

    # In this store, there are NO agents registered, so none are available.
    runner = CampaignRunner(repository=repo, agent_availability_store=agent_store)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    status = await runner.run_once(campaign_id="camp-test", now=now_utc)
    # The runner precheck finds no agents for campaign target states FL, TX
    assert status == "no_agents_available_precheck"

    # Check that lead attempts remained 0 and status is pending
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["attempts"] == 0
    assert updated_lead["status"] == "pending"


@pytest.mark.asyncio
async def test_wrong_number_is_final_and_never_retried(
    repo, default_campaign, default_lead, default_consent
):
    """Test that wrong number outcome sets status to wrong_number, releases lock, and is never retried."""
    await default_campaign(campaign_id="camp-test")
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    with mock.patch.dict(os.environ, {"DANA_CONFIRM_PLACE_CALL": "no"}):
        status = await runner.run_once(campaign_id="camp-test", now=now_utc, simulated_outcome="wrong_number")
        assert status == "completed_wrong_number"

    # Verify status is wrong_number and retry_after is None
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["status"] == "wrong_number"
    assert updated_lead["lock_holder_id"] is None
    assert updated_lead["retry_after"] is None


@pytest.mark.asyncio
async def test_callback_time_overrides_retry_policy(
    repo, default_campaign, default_lead, default_consent
):
    """Test that callback time scheduled on a lead overrides default retry policy rules."""
    await default_campaign(campaign_id="camp-test")
    
    # Lead has callback_time scheduled
    callback_dt = datetime(2026, 1, 1, 18, 0, 0, tzinfo=timezone.utc)
    await default_lead(
        lead_id="lead-123",
        campaign_id="camp-test",
        state="FL",
        status="callback",
        callback_time=callback_dt
    )
    await default_consent(lead_id="lead-123", campaign_id="camp-test")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    runner = CampaignRunner(repository=repo)
    
    # 1. Run step before callback time (e.g. 17:00). Lead should NOT be selected.
    status1 = await runner.run_once(campaign_id="camp-test", now=datetime(2026, 1, 1, 17, 0, 0, tzinfo=timezone.utc))
    assert status1 == "no_eligible_leads"

    # 2. Run step at or after callback time (18:00). Lead SHOULD be dialed.
    with mock.patch.dict(os.environ, {"DANA_CONFIRM_PLACE_CALL": "no"}):
        status2 = await runner.run_once(campaign_id="camp-test", now=datetime(2026, 1, 1, 18, 0, 0, tzinfo=timezone.utc))
        assert status2 == "success_human_answered"

    # Verify lead attempts incremented and callback_time is cleared (consumed)
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["attempts"] == 1
    assert updated_lead["callback_time"] is None


@pytest.mark.asyncio
async def test_dana_confirm_place_call_validation(
    repo, default_campaign, default_lead, default_consent
):
    """Test DANA_CONFIRM_PLACE_CALL = 'no' dry run behavior:

    - No real SIP participant creation happens
    - dry_run outcome result is logged and saved to telephony/last_outbound_call.json
    """
    await default_campaign(campaign_id="camp-test")
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    # Remove result file if exists
    if os.path.exists("telephony/last_outbound_call.json"):
        os.remove("telephony/last_outbound_call.json")

    with mock.patch.dict(os.environ, {"DANA_CONFIRM_PLACE_CALL": "no"}):
        status = await runner.run_once(campaign_id="camp-test", now=now_utc)
        assert status == "success_human_answered"

    # Verify dry run log is saved
    assert os.path.exists("telephony/last_outbound_call.json")
    with open("telephony/last_outbound_call.json", "r") as f:
        data = json.load(f)
        assert data["status"] == "dry_run"
        assert data["real_resource_created"] is False
        assert data["to"] == "+13055550199"
        assert data["from"] == "+18005550199"

    # Clean up
    if os.path.exists("telephony/last_outbound_call.json"):
        os.remove("telephony/last_outbound_call.json")


@pytest.mark.asyncio
async def test_real_mode_without_amd_result_instantly_bridges_call(
    repo, default_campaign, default_lead, default_consent
):
    """Test that in real mode, if no simulated outcome is provided,
    it instantly bridges the call (success_human_answered) to eliminate post-dial delay.
    """
    await default_campaign(campaign_id="camp-test")
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    # Set real place call to yes
    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    # Mock place_call to simulate a successful real SIP call placement (returning status: "placed")
    async def mock_place_call(lead, call_id, caller_id):
        return {
            "id": call_id,
            "status": "placed",
            "sip_participant_id": "sip-part-123",
            "to": lead.get("phone_e164"),
            "from": caller_id,
            "room_name": f"dana-call-{call_id[-8:]}"
        }

    runner.call_service.place_call = mock_place_call

    # Set environment variables for real placement and quick timeout
    env_patches = {
        "DANA_CONFIRM_PLACE_CALL": "yes",
        "DANA_AMD_TIMEOUT": "0.05"
    }

    with mock.patch.dict(os.environ, env_patches):
        status = await runner.run_once(campaign_id="camp-test", now=now_utc)
        assert status == "success_human_answered"

    # Verify lead status is completed
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["status"] == "completed"


@pytest.mark.asyncio
async def test_dry_run_mode_defaults_to_simulated_human_answered(
    repo, default_campaign, default_lead, default_consent
):
    """Test that in dry-run mode, if no simulated outcome is passed, it defaults to human_answered."""
    await default_campaign(campaign_id="camp-test")
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    # Mock place_call to return dry-run
    async def mock_place_call(lead, call_id, caller_id):
        return {
            "id": call_id,
            "status": "dry_run",
            "to": lead.get("phone_e164"),
            "from": caller_id,
            "room_name": f"dana-call-{call_id[-8:]}"
        }

    runner.call_service.place_call = mock_place_call

    # Run in dry-run mode
    with mock.patch.dict(os.environ, {"DANA_CONFIRM_PLACE_CALL": "no"}):
        status = await runner.run_once(campaign_id="camp-test", now=now_utc)
        assert status == "success_human_answered"

    # Verify lead status is completed
    updated_lead = await repo.get_lead("lead-123")
    assert updated_lead is not None
    assert updated_lead["status"] == "completed"


@pytest.mark.asyncio
async def test_human_answered_real_mode_calls_handoff_method(
    repo, default_campaign, default_lead, default_consent
):
    """Test that in real mode, if AMD returns human_answered, it invokes the LiveKit handoff method
    to update room metadata.
    """
    await default_campaign(campaign_id="camp-test")
    await default_lead(lead_id="lead-123", campaign_id="camp-test", state="FL")
    await default_consent(lead_id="lead-123", campaign_id="camp-test")
    await repo.save_caller_id(caller_id="+18005550199", campaign_id="camp-test", status="active")

    import asyncio
    runner = CampaignRunner(repository=repo)
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)

    # We mock place_call to simulate a successful real SIP call placement and concurrently schedule
    # writing the "human_answered" AMD outcome to the database.
    async def mock_place_call(lead, call_id, caller_id):
        async def simulate_webhook():
            await asyncio.sleep(0.01)
            await repo.save_call(
                call_id=call_id,
                amd_result="human_answered",
                outcome="human_answered"
            )
        asyncio.create_task(simulate_webhook())
        return {
            "id": call_id,
            "status": "placed",
            "sip_participant_id": "sip-part-123",
            "to": lead.get("phone_e164"),
            "from": caller_id,
            "room_name": f"dana-call-{call_id[-8:]}"
        }

    runner.call_service.place_call = mock_place_call

    # Mock LiveKitAPI to inspect the handoff update room metadata call
    with mock.patch("livekit.api.LiveKitAPI") as mock_lk_api_class:
        mock_lk_api = mock_lk_api_class.return_value
        mock_lk_api.room = mock.AsyncMock()
        mock_lk_api.aclose = mock.AsyncMock()

        env_patches = {
            "DANA_CONFIRM_PLACE_CALL": "yes",
            "DANA_AMD_TIMEOUT": "1.0"
        }

        with mock.patch.dict(os.environ, env_patches):
            status = await runner.run_once(campaign_id="camp-test", now=now_utc)
            assert status == "success_human_answered"

        # Verify update_room_metadata was called
        mock_lk_api.room.update_room_metadata.assert_called_once()
        
        # Verify the metadata argument contains campaign_id, lead_id and call_id
        called_kwargs = mock_lk_api.room.update_room_metadata.call_args.kwargs
        metadata_payload = json.loads(called_kwargs["metadata"])
        assert metadata_payload["campaign_id"] == "camp-test"
        assert metadata_payload["lead_id"] == "lead-123"
        assert "call_id" in metadata_payload

