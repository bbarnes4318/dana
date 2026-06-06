"""Tests for SpamRiskMonitor logic."""

from datetime import datetime, timezone
from dialer.spam_risk_monitor import SpamRiskMonitor


def test_spam_risk_monitor_low_risk():
    cid_metrics = {
        "caller_id": "+15551234",
        "total_calls": 100,
        "answer_rate": 0.15,
        "dnc_rate": 0.0,
        "total_dncs": 0,
        "total_complaints": 0
    }
    # 5 recent calls, 1 answered, duration 25 seconds (normal call)
    recent_calls = [
        {"outcome": "human_answered", "duration_seconds": 25.0},
        {"outcome": "no_answer", "duration_seconds": 0.0},
        {"outcome": "no_answer", "duration_seconds": 0.0},
        {"outcome": "voicemail", "duration_seconds": 15.0},
        {"outcome": "busy", "duration_seconds": 0.0},
    ]
    report = SpamRiskMonitor.calculate_spam_risk_score(cid_metrics, recent_calls)
    
    assert report.score < 0.3
    assert report.status == "low_risk"
    assert not report.answer_rate_drop_detected


def test_spam_risk_monitor_high_risk_short_hangups():
    cid_metrics = {
        "caller_id": "+15551234",
        "total_calls": 100,
        "answer_rate": 0.15,
        "dnc_rate": 0.0,
        "total_dncs": 0,
        "total_complaints": 0
    }
    # 5 recent calls, 4 answered but all hung up under 10 seconds
    recent_calls = [
        {"outcome": "human_answered", "duration_seconds": 4.5},
        {"outcome": "human_answered", "duration_seconds": 3.0},
        {"outcome": "human_answered", "duration_seconds": 5.0},
        {"outcome": "human_answered", "duration_seconds": 2.0},
        {"outcome": "no_answer", "duration_seconds": 0.0},
    ]
    report = SpamRiskMonitor.calculate_spam_risk_score(cid_metrics, recent_calls)
    
    assert report.short_call_hangup_rate == 1.0
    assert report.score >= 0.4
    # Since recent answer rate is 4/5 = 80%, there is no answer rate drop, but short hangups raise risk.


def test_spam_risk_monitor_high_risk_answer_rate_drop():
    cid_metrics = {
        "caller_id": "+15551234",
        "total_calls": 100,
        "answer_rate": 0.20,  # 20% average answer rate
        "dnc_rate": 0.0,
        "total_dncs": 0,
        "total_complaints": 0
    }
    # 10 recent calls, all no_answer (recent answer rate = 0%)
    recent_calls = [{"outcome": "no_answer", "duration_seconds": 0.0} for _ in range(10)]
    report = SpamRiskMonitor.calculate_spam_risk_score(cid_metrics, recent_calls)
    
    assert report.answer_rate_drop_detected
    assert report.score >= 0.4
