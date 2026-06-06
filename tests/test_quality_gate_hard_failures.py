"""Tests for PlatformScorecard hard failures logic."""

import os
from unittest.mock import patch
import pytest
from qa.platform_scorecard import PlatformScorecard


def get_base_mock_data() -> dict:
    """Returns a valid, passing base benchmark run results structure."""
    return {
        "run_id": "test_run_1",
        "timestamp": "2026-06-06T20:00:00Z",
        "slo_targets": {},
        "provider_reports": {
            "dana": {
                "provider_id": "dana",
                "provider_name": "Dana Local",
                "avg_p50_latency_ms": 400.0,
                "avg_p95_latency_ms": 800.0,
                "avg_humanlikeness_score": 95.0,
                "avg_overall_score": 92.0,
                "overall_grade": "A",
                "total_cost": 0.25,
                "scenario_results": {
                    "scenario_1": {
                        "scenario_id": "scenario_1",
                        "provider_id": "dana",
                        "p50_turn_latency_ms": 400.0,
                        "p95_turn_latency_ms": 800.0,
                        "tts_first_audio_ms": 150.0,
                        "llm_first_token_ms": 200.0,
                        "barge_in_stop_audio_ms": 150.0,
                        "bot_like_phrase_count": 0,
                        "repetition_count": 0,
                        "cost_per_connected_minute": 0.05,
                        "humanlikeness_score": 95.0,
                        "outcome_passed": True,
                        "overall_score": 92.0,
                        "overall_grade": "A",
                    }
                },
            }
        },
    }


def test_dnc_failure_triggers_hard_fail():
    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["dnc_failure_count"] = 1
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("DNC violation detected" in f for f in scorecard.evaluation["hard_fails"])


def test_wrong_number_failure_triggers_hard_fail():
    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["wrong_number_failure_count"] = 1
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Wrong-number violation detected" in f for f in scorecard.evaluation["hard_fails"])


def test_transfer_without_consent_triggers_hard_fail():
    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["transfer_without_consent_count"] = 1
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Transfer without consent detected" in f for f in scorecard.evaluation["hard_fails"])


def test_compliance_phrase_failure_triggers_hard_fail():
    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["compliance_hard_fail_count"] = 1
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Forbidden compliance phrase violation detected" in f for f in scorecard.evaluation["hard_fails"])


def test_missing_cost_accounting_triggers_hard_fail():
    data = get_base_mock_data()
    # Missing/0.0 cost per connected minute
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["cost_per_connected_minute"] = 0.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Missing cost accounting" in f for f in scorecard.evaluation["hard_fails"])


def test_production_mock_tts_triggers_hard_fail():
    data = get_base_mock_data()
    # Mocking production mode and allow_mock_tts enabled
    with patch("qa.platform_scorecard.is_production", return_value=True), \
         patch("qa.platform_scorecard.allow_mock_tts", return_value=True):
        scorecard = PlatformScorecard(data, provider_id="dana")
        assert scorecard.evaluation["passed"] is False
        assert any("Mock TTS (MockKokoro) is allowed/active in production" in f for f in scorecard.evaluation["hard_fails"])
