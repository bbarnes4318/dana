"""Tests for PlatformScorecard threshold evaluations."""

import pytest
from qa.platform_scorecard import PlatformScorecard, DEFAULT_THRESHOLDS


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


def test_scorecard_passing_by_default():
    data = get_base_mock_data()
    scorecard = PlatformScorecard(data, provider_id="dana")
    eval_res = scorecard.evaluation

    assert eval_res["passed"] is True
    assert len(eval_res["hard_fails"]) == 0
    assert len(eval_res["threshold_fails"]) == 0
    assert len(eval_res["threshold_warnings"]) == 0


def test_p50_turn_latency_thresholds():
    # Test warning threshold
    data = get_base_mock_data()
    # Warning limit: 450.0, Fail limit: 600.0
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["p50_turn_latency_ms"] = 500.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is True  # Warning doesn't fail the gate
    assert any("P50 Turn Latency" in w for w in scorecard.evaluation["threshold_warnings"])

    # Test fail threshold
    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["p50_turn_latency_ms"] = 650.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("P50 Turn Latency" in f for f in scorecard.evaluation["threshold_fails"])


def test_p95_turn_latency_thresholds():
    # Test warning threshold
    data = get_base_mock_data()
    # Warning limit: 850.0, Fail limit: 1000.0
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["p95_turn_latency_ms"] = 900.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is True
    assert any("P95 Turn Latency" in w for w in scorecard.evaluation["threshold_warnings"])

    # Test fail threshold
    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["p95_turn_latency_ms"] = 1050.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("P95 Turn Latency" in f for f in scorecard.evaluation["threshold_fails"])


def test_tts_first_audio_latency_thresholds():
    data = get_base_mock_data()
    # Warning limit: 200.0, Fail limit: 300.0
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["tts_first_audio_ms"] = 250.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is True
    assert any("Tts First Audio" in w for w in scorecard.evaluation["threshold_warnings"])

    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["tts_first_audio_ms"] = 350.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Tts First Audio" in f for f in scorecard.evaluation["threshold_fails"])


def test_llm_first_token_latency_thresholds():
    data = get_base_mock_data()
    # Warning limit: 250.0, Fail limit: 400.0
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["llm_first_token_ms"] = 300.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is True
    assert any("Llm First Token" in w for w in scorecard.evaluation["threshold_warnings"])

    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["llm_first_token_ms"] = 450.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Llm First Token" in f for f in scorecard.evaluation["threshold_fails"])


def test_barge_in_stop_latency_thresholds():
    data = get_base_mock_data()
    # Warning limit: 200.0, Fail limit: 300.0
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["barge_in_stop_audio_ms"] = 220.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is True
    assert any("Barge In Stop Audio" in w for w in scorecard.evaluation["threshold_warnings"])

    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["barge_in_stop_audio_ms"] = 320.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Barge In Stop Audio" in f for f in scorecard.evaluation["threshold_fails"])


def test_bot_like_phrase_count_thresholds():
    data = get_base_mock_data()
    # Warning limit: 1, Fail limit: 3
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["bot_like_phrase_count"] = 2
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is True
    assert any("Bot Like Phrase Count" in w for w in scorecard.evaluation["threshold_warnings"])

    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["bot_like_phrase_count"] = 4
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Bot Like Phrase Count" in f for f in scorecard.evaluation["threshold_fails"])


def test_repetition_count_thresholds():
    data = get_base_mock_data()
    # Warning limit: 1, Fail limit: 3
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["repetition_count"] = 2
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is True
    assert any("Repetition Count" in w for w in scorecard.evaluation["threshold_warnings"])

    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["repetition_count"] = 4
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Repetition Count" in f for f in scorecard.evaluation["threshold_fails"])


def test_cost_per_connected_minute_thresholds():
    data = get_base_mock_data()
    # Warning limit: 0.08, Fail limit: 0.15
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["cost_per_connected_minute"] = 0.10
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is True
    assert any("Cost Per Connected Minute" in w for w in scorecard.evaluation["threshold_warnings"])

    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["cost_per_connected_minute"] = 0.18
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Cost Per Connected Minute" in f for f in scorecard.evaluation["threshold_fails"])


def test_humanlikeness_score_thresholds():
    data = get_base_mock_data()
    # Warning limit: 85.0, Fail limit: 70.0 (Lower is worse)
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["humanlikeness_score"] = 80.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is True
    assert any("Humanlikeness Score" in w for w in scorecard.evaluation["threshold_warnings"])

    data = get_base_mock_data()
    data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["humanlikeness_score"] = 65.0
    scorecard = PlatformScorecard(data, provider_id="dana")
    assert scorecard.evaluation["passed"] is False
    assert any("Humanlikeness Score" in f for f in scorecard.evaluation["threshold_fails"])


def test_markdown_and_json_generation():
    data = get_base_mock_data()
    scorecard = PlatformScorecard(data, provider_id="dana")
    
    js = scorecard.generate_json()
    assert '"passed": true' in js
    
    md = scorecard.generate_markdown()
    assert "# Dana Platform Quality Scorecard:" in md
    assert "🟢 **PASSED**" in md
