"""Tests for TimezonePolicy logic."""

from datetime import datetime, timezone
import pytest
from dialer.timezone_policy import TimezonePolicy


def test_resolve_timezone_explicit():
    lead = {"callback_timezone": "America/Los_Angeles"}
    tz, source, conf = TimezonePolicy.resolve_timezone(lead)
    assert tz == "America/Los_Angeles"
    assert source == "explicit_timezone"


def test_resolve_timezone_state():
    lead = {"lead_state": "CA"}
    tz, source, conf = TimezonePolicy.resolve_timezone(lead)
    assert tz == "America/Los_Angeles"
    assert source == "lead_state"


def test_resolve_timezone_phone_area_code():
    lead = {"lead_phone_e164": "+13125551234"}  # Chicago area code 312
    tz, source, conf = TimezonePolicy.resolve_timezone(lead)
    assert tz == "America/Chicago"
    assert source == "area_code"


def test_get_local_time():
    utc_now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    # America/New_York is UTC-4 in June (DST)
    local_time = TimezonePolicy.get_local_time("America/New_York", utc_now)
    assert local_time.hour == 8
    assert local_time.minute == 0


def test_is_allowed_to_call_allowed_window():
    campaign = {
        "allowed_calling_hours": (9, 18),
        "allowed_days": ["mon", "tue", "wed", "thu", "fri"]
    }
    # 12:00 UTC = 8:00 AM America/New_York (Blocked by 9:00 AM campaign start)
    lead = {"callback_timezone": "America/New_York"}
    utc_now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)  # Monday
    assert not TimezonePolicy.is_allowed_to_call(lead, campaign, utc_now)

    # 14:00 UTC = 10:00 AM America/New_York (Allowed)
    utc_now = datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)
    assert TimezonePolicy.is_allowed_to_call(lead, campaign, utc_now)


def test_is_allowed_to_call_blocked_days():
    campaign = {
        "allowed_calling_hours": (9, 18),
        "allowed_days": ["mon", "tue", "wed", "thu", "fri"]
    }
    lead = {"callback_timezone": "America/New_York"}
    # 14:00 UTC on Sunday, June 7, 2026 (10:00 AM New York) -> Blocked by day
    utc_now = datetime(2026, 6, 7, 14, 0, tzinfo=timezone.utc)
    assert not TimezonePolicy.is_allowed_to_call(lead, campaign, utc_now)


def test_is_allowed_to_call_tcpa_early_morning_late_night():
    campaign = {
        "allowed_calling_hours": (6, 22),  # Tries to call 6 AM to 10 PM
        "allowed_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    }
    lead = {"callback_timezone": "America/New_York"}
    
    # 10:00 UTC = 6:00 AM New York -> Blocked by TCPA (must be >= 8 AM)
    utc_now = datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc)
    assert not TimezonePolicy.is_allowed_to_call(lead, campaign, utc_now)

    # 12:00 UTC = 8:00 AM New York -> Allowed
    utc_now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    assert TimezonePolicy.is_allowed_to_call(lead, campaign, utc_now)

    # 02:00 UTC (Next Day) = 10:00 PM New York -> Blocked by TCPA (must be < 9 PM)
    utc_now = datetime(2026, 6, 8, 2, 0, tzinfo=timezone.utc)
    assert not TimezonePolicy.is_allowed_to_call(lead, campaign, utc_now)
