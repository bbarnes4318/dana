import pytest
from datetime import datetime, timezone
from storage.repository import Repository
from telephony.campaign_service import TelephonyCampaignService


@pytest.mark.asyncio
async def test_create_provider_config():
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)
    
    config_id = await service.create_provider_config(
        name="Test Provider config",
        telnyx_phone_numbers=["+15555555555"]
    )
    assert config_id is not None
    
    config = await service.get_provider_config(config_id)
    assert config is not None
    assert config["name"] == "Test Provider config"
    assert config["telnyx_phone_numbers"] == ["+15555555555"]
    assert config["status"] == "draft"


@pytest.mark.asyncio
async def test_create_campaign_default_draft():
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)

    campaign_id = await service.create_campaign(
        name="Test Campaign June",
        operator="Jimmy"
    )
    assert campaign_id is not None

    campaign = await service.get_campaign(campaign_id)
    assert campaign is not None
    assert campaign["name"] == "Test Campaign June"
    assert campaign["status"] == "draft"
    assert campaign["daily_call_cap"] == 100
    assert campaign["max_concurrent_calls"] == 1


@pytest.mark.asyncio
async def test_campaign_ready_start_pause_resume_stop():
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)

    campaign_id = await service.create_campaign(name="Lifecycle Campaign", operator="Jimmy")

    # 1. Draft to Ready
    res = await service.mark_ready(campaign_id, operator="Jimmy", reason="Ready to dial")
    assert res.success is True
    assert res.new_status == "ready"

    # 2. Ready to Running
    res = await service.start_campaign(campaign_id, operator="Jimmy")
    assert res.success is True
    assert res.new_status == "running"

    # 3. Running to Paused
    res = await service.pause_campaign(campaign_id, operator="Jimmy")
    assert res.success is True
    assert res.new_status == "paused"

    # 4. Paused to Running (Resumed)
    res = await service.resume_campaign(campaign_id, operator="Jimmy")
    assert res.success is True
    assert res.new_status == "running"

    # 5. Running to Stopped
    res = await service.stop_campaign(campaign_id, operator="Jimmy")
    assert res.success is True
    assert res.new_status == "stopped"


@pytest.mark.asyncio
async def test_invalid_campaign_transitions_rejected():
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)

    campaign_id = await service.create_campaign(name="Draft Campaign", operator="Jimmy")

    # Cannot start directly from Draft (must go to Ready first)
    res = await service.start_campaign(campaign_id, operator="Jimmy")
    assert res.success is False
    assert res.error == "INVALID_TRANSITION"

    # Go to Ready
    await service.mark_ready(campaign_id, operator="Jimmy")

    # Cannot pause from Ready (must be Running)
    res = await service.pause_campaign(campaign_id, operator="Jimmy")
    assert res.success is False
    assert res.error == "INVALID_TRANSITION"


@pytest.mark.asyncio
async def test_operator_required_for_start_pause_stop():
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)

    campaign_id = await service.create_campaign(name="Operator Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")

    res = await service.start_campaign(campaign_id, operator="")
    assert res.success is False
    assert res.error == "OPERATOR_REQUIRED"


@pytest.mark.asyncio
async def test_control_event_written_on_transition():
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)

    campaign_id = await service.create_campaign(name="Audited Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy", reason="Ready check")

    events = await repository.query_campaign_control_events({"campaign_id": campaign_id})
    # Should have 'created' and 'ready' events
    assert len(events) == 2
    types = [e["event_type"] for e in events]
    assert "created" in types
    assert "ready" in types


@pytest.mark.asyncio
async def test_campaign_summary_counts():
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)

    campaign_id = await service.create_campaign(name="Summary Campaign", operator="Jimmy")
    
    # Save some dummy leads
    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15550001", status="new")
    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15550002", status="completed")
    await repository.save_campaign_lead(campaign_id=campaign_id, phone_number="+15550003", status="dnc")

    summary = await service.get_campaign_summary(campaign_id)
    assert summary.total_leads == 3
    assert summary.queued_leads == 1
    assert summary.completed_calls == 1
    assert summary.dnc_count == 1


@pytest.mark.asyncio
async def test_completed_campaign_cannot_restart():
    repository = Repository()
    service = TelephonyCampaignService(repository=repository)

    campaign_id = await service.create_campaign(name="Completed Campaign", operator="Jimmy")
    await service.mark_ready(campaign_id, operator="Jimmy")
    await service.start_campaign(campaign_id, operator="Jimmy")
    await service.complete_campaign(campaign_id, operator="Jimmy")

    # Cannot transition running from completed
    res = await service.start_campaign(campaign_id, operator="Jimmy")
    assert res.success is False
    assert res.error == "INVALID_TRANSITION"
