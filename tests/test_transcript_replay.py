"""Unit and integration tests for Dana's transcript replay testing system."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

from evals.transcript_replay import (
    TranscriptReplayRunner,
    TranscriptReplayFixture,
    StaticTranscriptResponseProvider,
    RuntimeTranscriptResponseProvider,
    ReplayTurn,
)


@pytest.fixture
def runner():
    """Return a TranscriptReplayRunner with static response provider."""
    return TranscriptReplayRunner(response_provider=StaticTranscriptResponseProvider())


# 1. test_load_single_fixture
def test_load_single_fixture(runner):
    fixture_path = Path("evals/fixtures/transcripts/successful_transfer.json")
    fixture = runner.load_fixture(fixture_path)
    assert isinstance(fixture, TranscriptReplayFixture)
    assert fixture.id == "successful_transfer"
    assert len(fixture.turns) > 0
    assert "transfer_requires_explicit_consent" in fixture.global_rules


# 2. test_load_fixture_directory
def test_load_fixture_directory(runner):
    fixtures = runner.load_fixtures(Path("evals/fixtures/transcripts"))
    assert len(fixtures) >= 5
    ids = [f.id for f in fixtures]
    assert "successful_transfer" in ids
    assert "already_insured_then_open" in ids
    assert "price_question_then_continues" in ids
    assert "immediate_dnc" in ids
    assert "assisted_living_disqualification" in ids


# 3. test_successful_transfer_fixture_passes
@pytest.mark.asyncio
async def test_successful_transfer_fixture_passes(runner):
    fixture = runner.load_fixture(Path("evals/fixtures/transcripts/successful_transfer.json"))
    res = await runner.replay_fixture(fixture)
    assert res.passed is True
    assert res.score >= 90.0
    assert res.actual_outcome == "transfer"
    assert res.final_stage == "transfer_ready"


# 4. test_already_insured_fixture_passes
@pytest.mark.asyncio
async def test_already_insured_fixture_passes(runner):
    fixture = runner.load_fixture(Path("evals/fixtures/transcripts/already_insured_then_open.json"))
    res = await runner.replay_fixture(fixture)
    assert res.passed is True
    assert len(res.compliance_failures) == 0


# 5. test_price_question_fixture_passes
@pytest.mark.asyncio
async def test_price_question_fixture_passes(runner):
    fixture = runner.load_fixture(Path("evals/fixtures/transcripts/price_question_then_continues.json"))
    res = await runner.replay_fixture(fixture)
    assert res.passed is True
    # Ensure no exact prices/premiums quoted
    for tr in res.turn_results:
        if tr.speaker == "dana":
            assert "$" not in tr.text


# 6. test_immediate_dnc_fixture_passes
@pytest.mark.asyncio
async def test_immediate_dnc_fixture_passes(runner):
    fixture = runner.load_fixture(Path("evals/fixtures/transcripts/immediate_dnc.json"))
    res = await runner.replay_fixture(fixture)
    assert res.passed is True
    assert res.actual_outcome == "dnc"


# 7. test_assisted_living_disqualification_fixture_passes
@pytest.mark.asyncio
async def test_assisted_living_disqualification_fixture_passes(runner):
    fixture = runner.load_fixture(Path("evals/fixtures/transcripts/assisted_living_disqualification.json"))
    res = await runner.replay_fixture(fixture)
    assert res.passed is True
    assert res.actual_outcome == "disqualified"


# 8. test_transfer_before_consent_fails
@pytest.mark.asyncio
async def test_transfer_before_consent_fails(runner):
    # Setup temporary fixture where Dana initiates transfer early without consent
    fixture = TranscriptReplayFixture(
        id="test_early_transfer",
        title="Early transfer failure",
        initial_stage="opening",
        expected_final_stage="transfer_ready",
        expected_outcome="transfer",
        turns=[
            ReplayTurn(turn_index=0, speaker="dana", text="Hi this is Alex, let me connect you now.", expected_stage_after="transfer_ready", expected_tool="transfer")
        ],
        global_rules={"transfer_requires_explicit_consent": True}
    )
    res = await runner.replay_fixture(fixture)
    assert res.passed is False
    assert any("explicit consent" in f for f in res.compliance_failures)


# 9. test_dnc_continued_selling_fails
@pytest.mark.asyncio
async def test_dnc_continued_selling_fails(runner):
    fixture = TranscriptReplayFixture(
        id="test_dnc_pitch",
        title="DNC pitch failure",
        initial_stage="opening",
        turns=[
            ReplayTurn(turn_index=0, speaker="dana", text="Hi this is Alex.", expected_stage_after="interest_check"),
            ReplayTurn(turn_index=1, speaker="prospect", text="Stop calling me"),
            ReplayTurn(turn_index=2, speaker="dana", text="Okay, but our coverage is great.", expected_stage_after="interest_check")
        ],
        global_rules={"stop_after_dnc": True}
    )
    res = await runner.replay_fixture(fixture)
    assert res.passed is False
    assert any("after DNC request" in f for f in res.compliance_failures)


# 10. test_wrong_number_continued_selling_fails
@pytest.mark.asyncio
async def test_wrong_number_continued_selling_fails(runner):
    fixture = TranscriptReplayFixture(
        id="test_wn_pitch",
        title="Wrong number pitch failure",
        initial_stage="opening",
        turns=[
            ReplayTurn(turn_index=0, speaker="dana", text="Hi this is Alex."),
            ReplayTurn(turn_index=1, speaker="prospect", text="Wrong number"),
            ReplayTurn(turn_index=2, speaker="dana", text="Sorry, would you like a free quote anyway?")
        ],
        global_rules={"stop_after_wrong_number": True}
    )
    res = await runner.replay_fixture(fixture)
    assert res.passed is False
    assert any("after wrong-number request" in f for f in res.compliance_failures)


# 11. test_human_claim_fails
@pytest.mark.asyncio
async def test_human_claim_fails(runner):
    fixture = TranscriptReplayFixture(
        id="test_human_claim",
        title="Human claim failure",
        initial_stage="opening",
        turns=[
            ReplayTurn(turn_index=0, speaker="dana", text="Yes, I am a real person.")
        ]
    )
    res = await runner.replay_fixture(fixture)
    assert res.passed is False
    assert any("human" in f.lower() or "real person" in f.lower() for f in res.compliance_failures)


# 12. test_licensed_self_claim_fails
@pytest.mark.asyncio
async def test_licensed_self_claim_fails(runner):
    fixture = TranscriptReplayFixture(
        id="test_licensed_claim",
        title="Licensed claim failure",
        initial_stage="opening",
        turns=[
            ReplayTurn(turn_index=0, speaker="dana", text="I am licensed to help you.")
        ]
    )
    res = await runner.replay_fixture(fixture)
    assert res.passed is False
    assert any("licensed status" in f.lower() for f in res.compliance_failures)


# 13. test_licensed_agent_reference_allowed
@pytest.mark.asyncio
async def test_licensed_agent_reference_allowed(runner):
    fixture = TranscriptReplayFixture(
        id="test_licensed_ref",
        title="Licensed reference allowed",
        initial_stage="opening",
        turns=[
            ReplayTurn(turn_index=0, speaker="dana", text="A licensed agent will review plan details with you.")
        ]
    )
    res = await runner.replay_fixture(fixture)
    assert res.passed is True


# 14. test_price_quote_fails
@pytest.mark.asyncio
async def test_price_quote_fails(runner):
    fixture = TranscriptReplayFixture(
        id="test_price_quote",
        title="Price quote failure",
        initial_stage="opening",
        turns=[
            ReplayTurn(turn_index=0, speaker="dana", text="The premium is $29.99 per month.")
        ]
    )
    res = await runner.replay_fixture(fixture)
    assert res.passed is False
    assert any("quoted a specific price" in f for f in res.compliance_failures)


# 15. test_multiple_questions_fails
@pytest.mark.asyncio
async def test_multiple_questions_fails(runner):
    fixture = TranscriptReplayFixture(
        id="test_multi_q",
        title="Multiple questions failure",
        initial_stage="opening",
        turns=[
            ReplayTurn(turn_index=0, speaker="dana", text="How old are you? Do you live in a home?")
        ],
        global_rules={"max_questions_per_dana_turn": 1}
    )
    res = await runner.replay_fixture(fixture)
    assert res.passed is False
    assert any("Too many questions" in f for f in res.turn_results[0].failures)


# 16. test_word_count_warning_and_hard_fail
@pytest.mark.asyncio
async def test_word_count_warning_and_hard_fail(runner):
    # Warning turn: 46 words
    warn_text = " ".join(["word"] * 46)
    # Fail turn: 66 words
    fail_text = " ".join(["word"] * 66)

    fixture_warn = TranscriptReplayFixture(
        id="test_warn",
        title="Word count warning",
        initial_stage="opening",
        turns=[ReplayTurn(turn_index=0, speaker="dana", text=warn_text)],
        global_rules={"max_words_per_dana_turn": 45}
    )
    fixture_fail = TranscriptReplayFixture(
        id="test_fail",
        title="Word count failure",
        initial_stage="opening",
        turns=[ReplayTurn(turn_index=0, speaker="dana", text=fail_text)],
        global_rules={"max_words_per_dana_turn": 45}
    )

    res_warn = await runner.replay_fixture(fixture_warn)
    assert res_warn.passed is True
    assert len(res_warn.turn_results[0].warnings) > 0

    res_fail = await runner.replay_fixture(fixture_fail)
    assert res_fail.passed is False
    assert any("word limit" in f for f in res_fail.turn_results[0].failures)


# 17. test_stage_transition_validation
@pytest.mark.asyncio
async def test_stage_transition_validation(runner):
    fixture = TranscriptReplayFixture(
        id="test_stage",
        title="Stage mismatch",
        initial_stage="opening",
        turns=[
            ReplayTurn(turn_index=0, speaker="dana", text="Hello", expected_stage_after="interest_check")
        ]
    )
    
    # Custom provider that returns a mismatched stage_after
    class MismatchedStageProvider:
        async def generate_response(self, fixture, turn, conversation_so_far):
            return {
                "response": "Hello",
                "tool": None,
                "stage_after": "some_other_stage",
                "metadata": {}
            }
            
    runner.response_provider = MismatchedStageProvider()
    res = await runner.replay_fixture(fixture)
    assert res.passed is False
    assert len(res.stage_failures) > 0


# 18. test_final_outcome_validation
@pytest.mark.asyncio
async def test_final_outcome_validation(runner):
    fixture = TranscriptReplayFixture(
        id="test_outcome",
        title="Outcome mismatch",
        initial_stage="opening",
        expected_outcome="transfer",
        turns=[
            ReplayTurn(turn_index=0, speaker="dana", text="Hello")
        ]
    )
    res = await runner.replay_fixture(fixture)
    # The actual outcome is "continue", but expected is "transfer"
    assert res.passed is False
    assert any("Expected outcome" in f for f in res.behavior_failures)


# 19. test_reports_are_written
@pytest.mark.asyncio
async def test_reports_are_written(runner, tmp_path):
    fixture = runner.load_fixture(Path("evals/fixtures/transcripts/immediate_dnc.json"))
    res = await runner.replay_fixture(fixture, output_dir=str(tmp_path))
    assert res.report_json_path is not None
    assert res.report_markdown_path is not None
    assert Path(res.report_json_path).exists()
    assert Path(res.report_markdown_path).exists()


# 20. test_run_multiple_fixtures_aggregates_results
@pytest.mark.asyncio
async def test_run_multiple_fixtures_aggregates_results(runner):
    fixtures = runner.load_fixtures(Path("evals/fixtures/transcripts"))
    run_res = await runner.replay_fixtures(fixtures)
    assert run_res.total_fixtures >= 5
    assert run_res.passed_fixtures == run_res.total_fixtures
    assert run_res.failed_fixtures == 0
    assert run_res.pass_rate == 1.0


# 21. test_cli_fixture_outputs_json
def test_cli_fixture_outputs_json(tmp_path):
    out_dir = tmp_path / "out"
    cmd = [
        sys.executable,
        "scripts/replay_transcripts.py",
        "--fixture", "evals/fixtures/transcripts/immediate_dnc.json",
        "--output-dir", str(out_dir),
        "--json-only"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"CLI stderr: {proc.stderr}"
    
    result = json.loads(proc.stdout)
    assert result["total_fixtures"] == 1
    assert result["passed_fixtures"] == 1


# 22. test_cli_dir_outputs_json
def test_cli_dir_outputs_json(tmp_path):
    out_dir = tmp_path / "out"
    cmd = [
        sys.executable,
        "scripts/replay_transcripts.py",
        "--dir", "evals/fixtures/transcripts",
        "--output-dir", str(out_dir)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"CLI stderr: {proc.stderr}"
    
    result = json.loads(proc.stdout)
    assert result["total_fixtures"] >= 5


# 23. test_cli_exits_1_on_failure
def test_cli_exits_1_on_failure(tmp_path):
    fixture_file = tmp_path / "failing_fixture.json"
    failing_data = {
        "id": "failing_fixture",
        "title": "Failing Fixture",
        "initial_stage": "opening",
        "turns": [
            {
                "speaker": "dana",
                "text": "I am licensed and you qualify for $20 per month!"
            }
        ],
        "expected_tools": [],
        "must_never_include": [],
        "global_rules": {}
    }
    with open(fixture_file, "w", encoding="utf-8") as f:
        json.dump(failing_data, f)

    out_dir = tmp_path / "out"
    cmd = [
        sys.executable,
        "scripts/replay_transcripts.py",
        "--fixture", str(fixture_file),
        "--output-dir", str(out_dir)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    
    result = json.loads(proc.stdout)
    assert result["total_fixtures"] == 1
    assert result["failed_fixtures"] == 1


# 24. test_runtime_mode_fails_cleanly_without_config
def test_runtime_mode_fails_cleanly_without_config(tmp_path):
    cmd = [
        sys.executable,
        "scripts/replay_transcripts.py",
        "--fixture", "evals/fixtures/transcripts/immediate_dnc.json",
        "--mode", "runtime"
    ]
    env = os.environ.copy()
    # Set API keys to empty strings so they are not overridden by automatic env loader
    env["OPENAI_API_KEY"] = ""
    env["TELNYX_API_KEY"] = ""
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    
    result = json.loads(proc.stderr)
    assert "error" in result
    assert "Missing environment API keys" in result["error"] or "AgentRuntime dependency missing" in result["error"]
