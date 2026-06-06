"""Tests for CampaignScheduler logic."""

from datetime import datetime, timedelta, timezone
import pytest
from dialer.campaign_scheduler import CampaignScheduler
from compliance.dnc_registry import InMemoryDNCRegistry


def test_is_campaign_active_basic():
    campaign = {
        "status": "running",
        "is_paused": False,
        "daily_call_cap": 100,
        "calls_started_today": 10,
        "timezone": "America/New_York",
        "allowed_days": ["mon", "tue", "wed", "thu", "fri"]
    }
    # Mon, June 8, 2026 14:00 UTC = 10:00 AM New York -> Active
    now = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)
    assert CampaignScheduler.is_campaign_active(campaign, now)

    # Pause
    campaign["is_paused"] = True
    assert not CampaignScheduler.is_campaign_active(campaign, now)


def test_is_campaign_active_cap_exceeded():
    campaign = {
        "status": "running",
        "is_paused": False,
        "daily_call_cap": 100,
        "calls_started_today": 100,
        "timezone": "America/New_York",
        "allowed_days": ["mon", "tue", "wed", "thu", "fri"]
    }
    now = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)
    assert not CampaignScheduler.is_campaign_active(campaign, now)


@pytest.mark.asyncio
async def test_is_lead_eligible_basic():
    campaign = {
        "max_attempts": 3,
        "allowed_calling_hours": (8, 20),
        "allowed_days": ["mon", "tue", "wed", "thu", "fri"]
    }
    lead = {
        "status": "queued",
        "attempt_count": 1,
        "callback_timezone": "America/New_York"
    }
    now = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 AM NY
    assert await CampaignScheduler.is_lead_eligible(lead, campaign, now)


@pytest.mark.asyncio
async def test_is_lead_eligible_max_attempts():
    campaign = {"max_attempts": 3}
    lead = {
        "status": "queued",
        "attempt_count": 3,
        "callback_timezone": "America/New_York"
    }
    now = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)
    assert not await CampaignScheduler.is_lead_eligible(lead, campaign, now)


@pytest.mark.asyncio
async def test_is_lead_eligible_cooldown():
    campaign = {"max_attempts": 3}
    lead = {
        "status": "queued",
        "attempt_count": 1,
        "callback_timezone": "America/New_York",
        "next_attempt_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    }
    now = datetime.now(timezone.utc)
    assert not await CampaignScheduler.is_lead_eligible(lead, campaign, now)


@pytest.mark.asyncio
async def test_is_lead_eligible_dnc_and_wrong_number():
    campaign = {"max_attempts": 3}
    lead_dnc = {
        "status": "dnc",
        "attempt_count": 0,
        "callback_timezone": "America/New_York"
    }
    lead_wn = {
        "status": "wrong_number",
        "attempt_count": 0,
        "callback_timezone": "America/New_York"
    }
    now = datetime.now(timezone.utc)
    assert not await CampaignScheduler.is_lead_eligible(lead_dnc, campaign, now)
    assert not await CampaignScheduler.is_lead_eligible(lead_wn, campaign, now)


@pytest.mark.asyncio
async def test_is_lead_eligible_dnc_registry_scrub():
    campaign = {"max_attempts": 3, "campaign_id": "c1"}
    lead = {
        "status": "queued",
        "attempt_count": 0,
        "phone_number": "+13125550199",
        "callback_timezone": "America/New_York"
    }
    dnc_reg = InMemoryDNCRegistry()
    await dnc_reg.add("+13125550199", reason="prospect request", campaign_id="c1")

    now = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)
    # Blocked by registry
    assert not await CampaignScheduler.is_lead_eligible(lead, campaign, now, dnc_reg)
