"""Tests for the Platform Quality Gate CLI entry point."""

import json
import os
import sys
import pytest
from unittest.mock import patch

@pytest.fixture(autouse=True)
def clean_env():
    with patch.dict(os.environ, {"DANA_RUNTIME_ENV": "development", "DANA_ALLOW_MOCK_TTS": "false"}):
        yield



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


def test_cli_missing_file(tmp_path):
    from qa.platform_quality_gate import main
    
    missing_file = str(tmp_path / "missing.json")
    test_args = ["platform_quality_gate", "--benchmark-file", missing_file]
    
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_cli_invalid_json(tmp_path):
    from qa.platform_quality_gate import main
    
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text("not json content")
    
    test_args = ["platform_quality_gate", "--benchmark-file", str(invalid_file)]
    
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_cli_passing_run(tmp_path):
    from qa.platform_quality_gate import main
    
    valid_file = tmp_path / "valid.json"
    valid_file.write_text(json.dumps(get_base_mock_data()))
    
    json_out = str(tmp_path / "scorecard.json")
    md_out = str(tmp_path / "scorecard.md")
    
    test_args = [
        "platform_quality_gate",
        "--benchmark-file", str(valid_file),
        "--json-output", json_out,
        "--md-output", md_out
    ]
    
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        
    assert os.path.exists(json_out)
    assert os.path.exists(md_out)


def test_cli_failing_run(tmp_path):
    from qa.platform_quality_gate import main
    
    failing_data = get_base_mock_data()
    # Trigger a hard fail (DNC failure)
    failing_data["provider_reports"]["dana"]["scenario_results"]["scenario_1"]["dnc_failure_count"] = 1
    
    invalid_run_file = tmp_path / "failing_run.json"
    invalid_run_file.write_text(json.dumps(failing_data))
    
    json_out = str(tmp_path / "scorecard.json")
    md_out = str(tmp_path / "scorecard.md")
    
    test_args = [
        "platform_quality_gate",
        "--benchmark-file", str(invalid_run_file),
        "--json-output", json_out,
        "--md-output", md_out
    ]
    
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
