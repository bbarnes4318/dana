"""Tests for DNC and wrong-number dialer suppression logic."""

from datetime import datetime, timezone
import pytest
from dialer.campaign_scheduler import CampaignScheduler
from compliance.dnc_registry import InMemoryDNCRegistry


@pytest.mark.asyncio
async def test_dnc_status_suppressed():
    campaign = {"max_attempts": 3}
    lead = {
        "status": "dnc",
        "attempt_count": 0,
        "callback_timezone": "America/New_York"
    }
    now = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)
    
    # Lead with "dnc" status must be suppressed
    eligible = await CampaignScheduler.is_lead_eligible(lead, campaign, now)
    assert not eligible


@pytest.mark.asyncio
async def test_wrong_number_status_suppressed():
    campaign = {"max_attempts": 3}
    lead = {
        "status": "wrong_number",
        "attempt_count": 0,
        "callback_timezone": "America/New_York"
    }
    now = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)
    
    # Lead with "wrong_number" status must be suppressed
    eligible = await CampaignScheduler.is_lead_eligible(lead, campaign, now)
    assert not eligible


@pytest.mark.asyncio
async def test_dnc_registry_suppression():
    campaign = {"max_attempts": 3, "campaign_id": "camp1"}
    lead = {
        "status": "queued",
        "attempt_count": 0,
        "phone_number": "+15550199",
        "callback_timezone": "America/New_York"
    }
    dnc_registry = InMemoryDNCRegistry()
    await dnc_registry.add("+15550199", reason="prospect requested dnc", campaign_id="camp1")
    now = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)

    # Lead phone is on DNC registry for this campaign -> must be blocked
    eligible = await CampaignScheduler.is_lead_eligible(lead, campaign, now, dnc_registry=dnc_registry)
    assert not eligible


@pytest.mark.asyncio
async def test_dnc_registry_global_suppression():
    campaign = {"max_attempts": 3, "campaign_id": "camp1"}
    lead = {
        "status": "queued",
        "attempt_count": 0,
        "phone_number": "+15550199",
        "callback_timezone": "America/New_York"
    }
    dnc_registry = InMemoryDNCRegistry()
    # Global DNC registration (campaign_id is None)
    await dnc_registry.add("+15550199", reason="national dnc registry")
    now = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)

    # Lead phone is on global DNC registry -> must be blocked for all campaigns
    eligible = await CampaignScheduler.is_lead_eligible(lead, campaign, now, dnc_registry=dnc_registry)
    assert not eligible
