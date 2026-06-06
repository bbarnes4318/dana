"""Tests for RetryPolicy logic."""

from datetime import datetime, timedelta, timezone
from dialer.retry_policy import RetryPolicy


def test_retry_policy_final_outcomes():
    campaign = {"max_attempts": 3}
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    
    # DNC, wrong number, hostile refusal, disconnected bad number should return None
    assert RetryPolicy.get_retry_after("dnc", campaign, 1, now) is None
    assert RetryPolicy.get_retry_after("wrong_number", campaign, 1, now) is None
    assert RetryPolicy.get_retry_after("hostile_refusal", campaign, 1, now) is None
    assert RetryPolicy.get_retry_after("disconnected_bad_number", campaign, 1, now) is None


def test_retry_policy_soft_outcomes():
    campaign = {
        "max_attempts": 3,
        "cooldown_no_answer": 3600,
        "cooldown_busy": 600,
        "voicemail_retry_allowed": True,
        "cooldown_voicemail": 7200
    }
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)

    # Busy -> now + 600s
    busy_retry = RetryPolicy.get_retry_after("busy", campaign, 1, now)
    assert busy_retry == now + timedelta(seconds=600)

    # No Answer -> now + 3600s
    na_retry = RetryPolicy.get_retry_after("no_answer", campaign, 1, now)
    assert na_retry == now + timedelta(seconds=3600)

    # Voicemail -> now + 7200s
    vm_retry = RetryPolicy.get_retry_after("voicemail", campaign, 1, now)
    assert vm_retry == now + timedelta(seconds=7200)


def test_retry_policy_voicemail_blocked():
    campaign = {
        "max_attempts": 3,
        "voicemail_retry_allowed": False
    }
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    assert RetryPolicy.get_retry_after("voicemail", campaign, 1, now) is None


def test_retry_policy_max_attempts():
    campaign = {"max_attempts": 3, "cooldown_busy": 600}
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    # 3 attempts >= 3 max_attempts -> returns None
    assert RetryPolicy.get_retry_after("busy", campaign, 3, now) is None


def test_retry_policy_callback_override():
    campaign = {"max_attempts": 3}
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    callback = datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)
    # Callback time overrides normal busy retry policy
    assert RetryPolicy.get_retry_after("busy", campaign, 1, now, callback_time=callback) == callback
