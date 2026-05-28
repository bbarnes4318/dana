"""Unit tests for dialer integration compliance checking."""

from __future__ import annotations

import os
import sys
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from compliance.consent_record import ConsentRecord
from compliance.dnc_registry import InMemoryDNCRegistry
from dialer.pre_call_check import verify_pre_call


@pytest.mark.asyncio
async def test_verify_pre_call_allowed() -> None:
    lead = {
        "lead_id": "lead-allowed",
        "lead_phone_e164": "+13050000000",
        "campaign_id": "camp-1",
        "lead_state": "FL"
    }
    campaign = {
        "campaign_id": "camp-1",
        "is_paused": False,
        "approved_consent_sources": ["trustedform"],
        "max_attempts": 3,
        "caller_id": "+18005550199",
        "active_caller_ids": ["+18005550199"]
    }
    consent = ConsentRecord(
        consent_artifact_id="art-1",
        lead_id="lead-allowed",
        phone_e164="+13050000000",
        source_vendor="trustedform",
        consent_text="Agree to call",
        consent_timestamp="2026-05-27T12:00:00Z"
    )
    dnc = InMemoryDNCRegistry()
    now_utc = datetime(2026, 5, 27, 15, 0, 0, tzinfo=timezone.utc)

    decision = await verify_pre_call(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is True
    assert decision.reason == "Approved for dialing"


@pytest.mark.asyncio
async def test_verify_pre_call_blocked() -> None:
    lead = {
        "lead_id": "lead-blocked",
        "lead_phone_e164": "+13050000000",
        "campaign_id": "camp-1",
        "lead_state": "FL"
    }
    campaign = {
        "campaign_id": "camp-1",
        "is_paused": True, # Paused campaign
        "approved_consent_sources": ["trustedform"],
        "max_attempts": 3,
        "caller_id": "+18005550199",
        "active_caller_ids": ["+18005550199"]
    }
    consent = ConsentRecord(
        consent_artifact_id="art-1",
        lead_id="lead-blocked",
        phone_e164="+13050000000",
        source_vendor="trustedform",
        consent_text="Agree to call",
        consent_timestamp="2026-05-27T12:00:00Z"
    )
    dnc = InMemoryDNCRegistry()
    now_utc = datetime(2026, 5, 27, 15, 0, 0, tzinfo=timezone.utc)

    decision = await verify_pre_call(lead, campaign, consent, dnc, now=now_utc)
    assert decision.allowed is False
    assert "campaign_paused" in decision.blocked_by
