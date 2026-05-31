import pytest
import os
from datetime import datetime, timezone
from storage.repository import Repository
from telephony.dialer_queue import DialerQueue, DialerTickConfig
from telephony.campaign_service import TelephonyCampaignService
from telephony.livekit_adapter import LiveKitOutboundAdapter


from unittest.mock import AsyncMock
from telephony.livekit_adapter import LiveKitOutboundAdapter, LiveKitDialResult


@pytest.fixture
def mock_adapter():
    return LiveKitOutboundAdapter()


@pytest.mark.asyncio
async def test_no_dial_if_campaign_not_running(mock_adapter):
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    # Draft status by default
    campaign_id = await service.create_campaign(name="Draft Campaign", operator="Jimmy")
    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=False)
    res = await dialer.run_tick(config)
    assert res.calls_started == 0
    assert "Campaign is not running" in res.warnings[0]


@pytest.mark.asyncio
async def test_dry_run_does_not_create_attempts(mock_adapter):
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Running Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")
    
    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    # dry_run = True
    config = DialerTickConfig(campaign_id=campaign_id, dry_run=True, live_mode=False, force=True)
    res = await dialer.run_tick(config)
    assert res.eligible_leads == 1
    assert res.calls_started == 0
    assert len(res.attempt_ids) == 0

    # No attempts should be created in DB
    attempts = await repository.query_call_attempts({"campaign_id": campaign_id})
    assert len(attempts) == 0


@pytest.mark.asyncio
async def test_mock_tick_creates_attempts_when_not_live(mock_adapter):
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Mock Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")

    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    # dry_run = False, live_mode = False
    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=False, force=True)
    res = await dialer.run_tick(config)
    assert res.calls_started == 1
    assert len(res.attempt_ids) == 1

    # Attempt is recorded
    attempts = await repository.query_call_attempts({"campaign_id": campaign_id})
    assert len(attempts) == 1
    assert attempts[0]["status"] == "completed"  # Mock defaults to completed
    assert attempts[0]["outcome"] == "answered"


@pytest.mark.asyncio
async def test_respects_max_concurrency(mock_adapter):
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(
        name="Concurrent Campaign",
        max_concurrent_calls=1,
        operator="Jimmy"
    )
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")

    # Save active session matching campaign
    await repository.save_live_call_session(
        campaign_id=campaign_id,
        lead_id="lead_1",
        attempt_id="att_1",
        call_id="call_1",
        status="active"
    )

    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=False, force=True)
    res = await dialer.run_tick(config)
    assert res.calls_started == 0
    assert "concurrency limit" in res.warnings[0]


@pytest.mark.asyncio
async def test_respects_daily_cap(mock_adapter):
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(
        name="Daily Cap Campaign",
        daily_call_cap=2,
        operator="Jimmy"
    )
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")

    # Create 2 previous attempts today
    now_str = datetime.now(timezone.utc).isoformat()
    await repository.save_call_attempt(campaign_id=campaign_id, lead_id="l1", status="completed", started_at=now_str)
    await repository.save_call_attempt(campaign_id=campaign_id, lead_id="l2", status="completed", started_at=now_str)

    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=False, force=True)
    res = await dialer.run_tick(config)
    assert res.calls_started == 0
    assert "daily call cap" in res.warnings[0]


@pytest.mark.asyncio
async def test_skips_dnc_wrong_number_suppressed_leads(mock_adapter):
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Clean Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")

    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="dnc")
    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550002", status="wrong_number")

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=False, force=True)
    res = await dialer.run_tick(config)
    assert res.eligible_leads == 0
    assert res.calls_started == 0


@pytest.mark.asyncio
async def test_skips_leads_over_max_attempts(mock_adapter):
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Max Attempts Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")

    await repository.save_campaign_lead(
        campaign_id=campaign_id,
        phone_number="+15555550001",
        status="new",
        attempt_count=3,
        max_attempts=3
    )

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=False, force=True)
    res = await dialer.run_tick(config)
    assert res.eligible_leads == 0
    assert res.calls_started == 0


@pytest.mark.asyncio
async def test_live_mode_requires_env_flags(tmp_path, mock_adapter):
    repository = Repository(data_dir=tmp_path)
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Live Mode Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")

    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    # Clear env flags
    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "DANA_ENABLE_OUTBOUND_DIALER"]}
    os.environ["TELEPHONY_LIVE_MODE"] = "false"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "false"

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=True, force=True)
    res = await dialer.run_tick(config)
    # Dialer should reject live mode because environment keys are false
    assert res.calls_started == 0
    assert "Live mode is not enabled in this environment" in res.errors[0]

    for k, v in orig.items():
        if v is not None: os.environ[k] = v
        elif k in os.environ: del os.environ[k]


@pytest.mark.asyncio
async def test_live_tick_requires_provider_or_env_trunk(tmp_path, mock_adapter):
    repository = Repository(data_dir=tmp_path)
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Trunk Test Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")
    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    # Set live mode env flags to true
    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "DANA_ENABLE_OUTBOUND_DIALER", "LIVEKIT_SIP_OUTBOUND_TRUNK_ID"]}
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    if "LIVEKIT_SIP_OUTBOUND_TRUNK_ID" in os.environ:
        del os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"]

    # Run tick
    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=True, force=True)
    res = await dialer.run_tick(config)

    # Should fail due to missing trunk ID
    assert res.calls_started == 0
    assert any("missing outbound trunk ID" in e for e in res.errors)

    # Restore env
    for k, v in orig.items():
        if v is not None: os.environ[k] = v
        elif k in os.environ: del os.environ[k]


@pytest.mark.asyncio
async def test_live_tick_calls_adapter_when_live_enabled(tmp_path, mock_adapter):
    repository = Repository(data_dir=tmp_path)
    service = TelephonyCampaignService(repository=repository)
    
    mock_dial = AsyncMock(return_value=LiveKitDialResult(
        success=True,
        dry_run=False,
        live_mode=True,
        room_name="test-room",
        participant_identity="test-identity",
        livekit_participant_id="part-123",
        livekit_sip_call_id="sip-123",
        message=""
    ))
    mock_adapter.dial = mock_dial

    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Adapter Call Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")
    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "DANA_ENABLE_OUTBOUND_DIALER", "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "DANA_OUTBOUND_CALLER_ID"]}
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-123"
    os.environ["DANA_OUTBOUND_CALLER_ID"] = "+15550000"

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=True, force=True)
    res = await dialer.run_tick(config)

    assert res.calls_started == 1
    mock_dial.assert_called_once()

    for k, v in orig.items():
        if v is not None: os.environ[k] = v
        elif k in os.environ: del os.environ[k]


@pytest.mark.asyncio
async def test_live_tick_updates_attempt_success(tmp_path, mock_adapter):
    repository = Repository(data_dir=tmp_path)
    service = TelephonyCampaignService(repository=repository)
    
    mock_adapter.dial = AsyncMock(return_value=LiveKitDialResult(
        success=True,
        dry_run=False,
        live_mode=True,
        room_name="test-room",
        participant_identity="test-identity",
        livekit_participant_id="part-success",
        livekit_sip_call_id="sip-success",
        answered=True,
        message=""
    ))

    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Success Attempt Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")
    lead_id = "lead_success_1"
    await repository.save_campaign_lead(
        id=lead_id,
        campaign_id=campaign_id,
        phone_number="+15555550001",
        status="new"
    )

    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "DANA_ENABLE_OUTBOUND_DIALER", "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "DANA_OUTBOUND_CALLER_ID"]}
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-123"
    os.environ["DANA_OUTBOUND_CALLER_ID"] = "+15550000"

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=True, force=True)
    res = await dialer.run_tick(config)

    assert res.calls_started == 1
    
    # Check attempt updated
    attempts = await repository.query_call_attempts({"campaign_id": campaign_id})
    assert len(attempts) == 1
    assert attempts[0]["status"] == "answered"
    assert attempts[0]["livekit_participant_id"] == "part-success"
    assert attempts[0]["livekit_sip_call_id"] == "sip-success"

    # Lead should be in_call
    lead = await repository.get_campaign_lead(lead_id)
    assert lead["status"] == "in_call"

    for k, v in orig.items():
        if v is not None: os.environ[k] = v
        elif k in os.environ: del os.environ[k]


@pytest.mark.asyncio
async def test_live_tick_updates_attempt_failure(tmp_path, mock_adapter):
    repository = Repository(data_dir=tmp_path)
    service = TelephonyCampaignService(repository=repository)
    
    mock_adapter.dial = AsyncMock(return_value=LiveKitDialResult(
        success=False,
        dry_run=False,
        live_mode=True,
        room_name="test-room",
        participant_identity="test-identity",
        message="Trunk authentication failed",
        sip_status_code=401,
        sip_status="UNAUTHORIZED"
    ))

    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Failure Attempt Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")
    lead_id = "lead_fail_1"
    await repository.save_campaign_lead(
        id=lead_id,
        campaign_id=campaign_id,
        phone_number="+15555550001",
        status="new"
    )

    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "DANA_ENABLE_OUTBOUND_DIALER", "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "DANA_OUTBOUND_CALLER_ID"]}
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"
    os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] = "trunk-123"
    os.environ["DANA_OUTBOUND_CALLER_ID"] = "+15550000"

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=True, force=True)
    res = await dialer.run_tick(config)

    assert res.calls_started == 0
    assert len(res.errors) == 1

    attempts = await repository.query_call_attempts({"campaign_id": campaign_id})
    assert len(attempts) == 1
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["failure_reason"] == "Trunk authentication failed"
    assert attempts[0]["sip_status_code"] == 401

    # Lead should be queued since it is retryable
    lead = await repository.get_campaign_lead(lead_id)
    assert lead["status"] == "queued"

    for k, v in orig.items():
        if v is not None: os.environ[k] = v
        elif k in os.environ: del os.environ[k]


@pytest.mark.asyncio
async def test_live_tick_never_silent_mocks_when_live_requested(tmp_path, mock_adapter):
    repository = Repository(data_dir=tmp_path)
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Silent Mock Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")
    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    # If we request live mode, but live mode env is false, it MUST fail and not silently fallback to mock
    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "DANA_ENABLE_OUTBOUND_DIALER"]}
    os.environ["TELEPHONY_LIVE_MODE"] = "false"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "false"

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=True, force=True)
    res = await dialer.run_tick(config)

    assert res.calls_started == 0
    assert "Live mode is not enabled in this environment" in res.errors[0]

    for k, v in orig.items():
        if v is not None: os.environ[k] = v
        elif k in os.environ: del os.environ[k]


@pytest.mark.asyncio
async def test_live_tick_respects_caps_before_adapter_call(tmp_path, mock_adapter):
    repository = Repository(data_dir=tmp_path)
    service = TelephonyCampaignService(repository=repository)
    
    mock_dial = AsyncMock()
    mock_adapter.dial = mock_dial

    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(
        name="Daily Cap Live Campaign",
        daily_call_cap=1,
        operator="Jimmy"
    )
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")

    # Today's attempt already exists
    now_str = datetime.now(timezone.utc).isoformat()
    await repository.save_call_attempt(campaign_id=campaign_id, lead_id="lead_1", status="completed", started_at=now_str)

    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    orig = {k: os.environ.get(k) for k in ["TELEPHONY_LIVE_MODE", "DANA_ENABLE_OUTBOUND_DIALER"]}
    os.environ["TELEPHONY_LIVE_MODE"] = "true"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "true"

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=True, force=True)
    res = await dialer.run_tick(config)

    assert res.calls_started == 0
    assert "daily call cap" in res.warnings[0]
    # Adapter must not have been called because cap check triggered first
    mock_dial.assert_not_called()

    for k, v in orig.items():
        if v is not None: os.environ[k] = v
        elif k in os.environ: del os.environ[k]

