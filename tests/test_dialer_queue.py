import pytest
import os
from datetime import datetime, timezone
from storage.repository import Repository
from telephony.dialer_queue import DialerQueue, DialerTickConfig
from telephony.campaign_service import TelephonyCampaignService
from telephony.livekit_adapter import LiveKitOutboundAdapter


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
async def test_live_mode_requires_env_flags(mock_adapter):
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)
    dialer = DialerQueue(repository=repository, adapter=mock_adapter)

    campaign_id = await service.create_campaign(name="Live Mode Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")

    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15555550001", status="new")

    # Clear env flags
    os.environ["TELEPHONY_LIVE_MODE"] = "false"
    os.environ["DANA_ENABLE_OUTBOUND_DIALER"] = "false"

    config = DialerTickConfig(campaign_id=campaign_id, dry_run=False, live_mode=True, force=True)
    res = await dialer.run_tick(config)
    # Dialer should reject live mode because environment keys are false
    assert res.calls_started == 0
    assert "Live mode is not enabled in this environment" in res.errors[0]
