"""Unit tests for compliance calling window checks and timezone resolution."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from compliance.calling_window import (
    resolve_lead_timezone,
    is_calling_window_allowed
)


def test_timezone_resolution_callback_tz() -> None:
    # 1. callback_timezone takes highest priority
    lead = {
        "callback_timezone": "America/New_York",
        "lead_state": "CA",
        "lead_phone_e164": "+12130000000"
    }
    assert resolve_lead_timezone(lead) == ("America/New_York", "explicit_timezone", "high")


def test_timezone_resolution_state() -> None:
    # 2. lead_state mapping takes next priority
    lead = {
        "callback_timezone": None,
        "lead_state": "CA",
        "lead_phone_e164": "+13050000000" # Miami area code, but state is CA
    }
    # CA is a single-timezone state -> medium/high
    assert resolve_lead_timezone(lead) == ("America/Los_Angeles", "lead_state", "medium/high")
    
    # State parameter check (alternative key)
    lead_alt = {
        "state": "tx"
    }
    # TX is a multi-timezone state -> medium
    assert resolve_lead_timezone(lead_alt) == ("America/Chicago", "lead_state", "medium")

    # Verified city/state -> high
    lead_verified = {
        "state": "CA",
        "verified_city_state": True
    }
    assert resolve_lead_timezone(lead_verified) == ("America/Los_Angeles", "lead_state", "high")


def test_timezone_resolution_area_code() -> None:
    # 3. area code fallback
    lead = {
        "lead_phone_e164": "+13050000000"
    }
    assert resolve_lead_timezone(lead) == ("America/New_York", "area_code", "low")
    
    lead_la = {
        "lead_phone_e164": "+12130000000"
    }
    assert resolve_lead_timezone(lead_la) == ("America/Los_Angeles", "area_code", "low")


def test_timezone_resolution_unresolvable() -> None:
    # 4. Returns None if completely missing or unmapped
    lead = {}
    assert resolve_lead_timezone(lead) == (None, "unknown", "unknown/low")
    
    lead_invalid_phone = {
        "lead_phone_e164": "999"
    }
    assert resolve_lead_timezone(lead_invalid_phone) == (None, "unknown", "unknown/low")


def test_is_calling_window_allowed() -> None:
    # Test checking calling window allowed hours
    # Construct a local target hour using custom UTC time.
    # America/New_York is UTC-5 (or UTC-4 in daylight savings).
    # Let's use winter timezone offset (UTC-5) to be standard:
    # If it is 15:00 UTC (3:00 PM UTC), then in America/New_York (UTC-5) it is 10:00 AM.
    # 10:00 AM local time should be allowed for a window of (8, 20).
    now_utc = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    allowed = is_calling_window_allowed(
        timezone_str="America/New_York",
        allowed_hours=(8, 20),
        current_utc_time=now_utc
    )
    assert allowed is True

    # If it is 2:00 UTC (2:00 AM UTC), then in America/New_York (UTC-5) it is 9:00 PM local (21:00).
    # Local time 9:00 PM (21:00) is outside the (8, 20) window (8:00 AM to 8:00 PM).
    now_utc_night = datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc)
    allowed_night = is_calling_window_allowed(
        timezone_str="America/New_York",
        allowed_hours=(8, 20),
        current_utc_time=now_utc_night
    )
    assert allowed_night is False
