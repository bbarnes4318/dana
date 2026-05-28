"""Unit tests for PreDialGate compliance checker."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from compliance.consent_record import ConsentRecord
from compliance.dnc_registry import InMemoryDNCRegistry
from compliance.pre_dial_gate import PreDialGate


@pytest.fixture
def base_setup() -> tuple[dict, dict, ConsentRecord, InMemoryDNCRegistry]:
    lead = {
        "lead_id": "lead-123",
        "lead_phone_e164": "+13050000000",
        "campaign_id": "camp-xyz",
        "lead_state": "FL",
        "attempts": 0
    }
    campaign = {
        "campaign_id": "camp-xyz",
        "is_paused": False,
        "approved_consent_sources": ["trustedform", "landing_page"],
        "max_attempts": 3,
        "allowed_calling_hours": (8, 20),
        "caller_id": "+18005550199",
        "active_caller_ids": ["+18005550199"]
    }
    consent = ConsentRecord(
        consent_artifact_id="art-999",
        lead_id="lead-123",
        phone_e164="+13050000000",
        source_vendor="trustedform",
        consent_text="Agree to marketing",
        consent_timestamp="2026-05-27T12:00:00Z",
        campaign_id="camp-xyz"
    )
    dnc = InMemoryDNCRegistry()
    return lead, campaign, consent, dnc


@pytest.mark.asyncio
async def test_gate_happy_path(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    gate = PreDialGate()
    
    # 15:00 UTC is 10:00 AM local time in Florida (Eastern Time).
    # Allowed hours are (8, 20).
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    
    decision = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is True
    assert decision.reason == "Approved for dialing"
    assert len(decision.blocked_by) == 0


@pytest.mark.asyncio
async def test_gate_missing_phone(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    lead["lead_phone_e164"] = None
    gate = PreDialGate()
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    
    decision = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is False
    assert "missing_phone_e164" in decision.blocked_by


@pytest.mark.asyncio
async def test_gate_campaign_paused(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    campaign["is_paused"] = True
    gate = PreDialGate()
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    
    decision = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is False
    assert "campaign_paused" in decision.blocked_by


@pytest.mark.asyncio
async def test_gate_missing_consent(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    gate = PreDialGate()
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    
    decision = await gate.check(lead, campaign, None, dnc, now=now_utc)
    assert decision.allowed is False
    assert "missing_consent_record" in decision.blocked_by


@pytest.mark.asyncio
async def test_gate_consent_vendor_unapproved(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    consent.source_vendor = "unapproved_broker"
    gate = PreDialGate()
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    
    decision = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is False
    assert "consent_source_not_approved" in decision.blocked_by


@pytest.mark.asyncio
async def test_gate_global_dnc(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    # Add number to global DNC list
    await dnc.add("+13050000000", "Prospect requested DNC", campaign_id=None)
    
    gate = PreDialGate()
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    
    decision = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is False
    assert "phone_on_dnc" in decision.blocked_by


@pytest.mark.asyncio
async def test_gate_campaign_dnc(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    # Add number to campaign DNC list
    await dnc.add("+13050000000", "Campaign opt out", campaign_id="camp-xyz")
    
    gate = PreDialGate()
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    
    decision = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is False
    assert "phone_on_dnc" in decision.blocked_by


@pytest.mark.asyncio
async def test_gate_exceeded_attempts(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    lead["attempts"] = 3 # Campaign max_attempts is 3
    gate = PreDialGate()
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    
    decision = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is False
    assert "exceeded_max_attempts" in decision.blocked_by


@pytest.mark.asyncio
async def test_gate_outside_calling_window(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    gate = PreDialGate()
    # 2:00 AM UTC in winter is 9:00 PM local time in Florida (Eastern Time).
    # Since standard/allowed window is 8 AM to 8 PM (8, 20), 9:00 PM is outside.
    now_utc = datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc)
    
    decision = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is False
    assert "outside_calling_window" in decision.blocked_by


@pytest.mark.asyncio
async def test_gate_missing_timezone_resolving(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    lead["lead_state"] = None
    lead["lead_phone_e164"] = "999" # invalid area code lookup
    gate = PreDialGate()
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    
    decision = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is False
    assert "missing_timezone_no_fallback" in decision.blocked_by


@pytest.mark.asyncio
async def test_gate_caller_id_checks(base_setup) -> None:
    lead, campaign, consent, dnc = base_setup
    
    # Caller ID missing
    campaign["caller_id"] = None
    gate = PreDialGate()
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    
    decision1 = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision1.allowed is False
    assert "caller_id_missing" in decision1.blocked_by
    
    # Caller ID inactive
    campaign["caller_id"] = "+18881234567"
    campaign["active_caller_ids"] = ["+18005550199"]
    decision2 = await gate.check(lead, campaign, consent, dnc, now=now_utc)
    assert decision2.allowed is False
    assert "caller_id_inactive" in decision2.blocked_by
