import pytest
from benchmarks.voice_platform_benchmark.score_compliance import score_compliance
from benchmarks.voice_platform_benchmark.run_transcript_replay import run_transcript_replay

def test_dnc_pitching_fails_gate():
    transcript = [
        {"speaker": "prospect", "text": "Stop calling me, remove me from your list."},
        {"speaker": "dana", "text": "Sure, but I want to mention our final expense plan has great rates!"}
    ]
    res = score_compliance(transcript)
    assert res["dnc_failure_count"] == 1
    assert len(res["compliance_messages"]) == 1

def test_wrong_number_pitching_fails_gate():
    transcript = [
        {"speaker": "prospect", "text": "This is a wrong number."},
        {"speaker": "dana", "text": "Oh, sorry. Do you have a spouse who might want life insurance coverage?"}
    ]
    res = score_compliance(transcript)
    assert res["wrong_number_failure_count"] == 1

def test_transfer_without_consent_fails_gate():
    # Transcript where Dana transfers without the user saying yes/okay/sure/etc.
    transcript = [
        {"speaker": "prospect", "text": "What is this about?"},
        {"speaker": "dana", "text": "I'll connect you with a licensed agent now.", "tool": "feTransfer"}
    ]
    res = score_compliance(transcript)
    assert res["transfer_without_consent_count"] == 1

def test_forbidden_phrases_price_fails_gate():
    transcript = [
        {"speaker": "dana", "text": "The rate is only $29.99 per month."}
    ]
    res = score_compliance(transcript)
    assert res["compliance_hard_fail_count"] == 1

def test_forbidden_phrases_licensed_fails_gate():
    transcript = [
        {"speaker": "dana", "text": "I am a licensed agent in your state."}
    ]
    res = score_compliance(transcript)
    assert res["compliance_hard_fail_count"] == 1

def test_forbidden_phrases_government_fails_gate():
    transcript = [
        {"speaker": "dana", "text": "We are with the government program."}
    ]
    res = score_compliance(transcript)
    assert res["compliance_hard_fail_count"] == 1

def test_forbidden_phrases_guarantee_fails_gate():
    transcript = [
        {"speaker": "dana", "text": "Your approval is guaranteed."}
    ]
    res = score_compliance(transcript)
    assert res["compliance_hard_fail_count"] == 1

def test_run_transcript_replay_forces_grade_f_on_compliance_failure():
    transcript = [
        {"speaker": "prospect", "text": "Hello?"},
        {"speaker": "dana", "text": "I am licensed and it costs $10 a month.", "metadata": {}}
    ]
    
    # Run transcript replay
    provider_config = {
        "cost_per_connected_minute": 0.05,
        "cost_per_qualified_transfer": 0.0
    }
    
    metrics = run_transcript_replay(
        provider_id="test_provider",
        scenario_id="normal_interested",
        transcript=transcript,
        provider_config=provider_config,
        expected_outcome="transferred"
    )
    
    assert metrics.compliance_hard_fail_count > 0
    assert metrics.overall_grade == "F"
    assert metrics.overall_score == 0.0
