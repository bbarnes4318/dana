"""Unit and integration tests for Dana's deterministic eval case runner."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

from storage.repository import Repository
from evals.case_runner import (
    EvalCaseRunner,
    EvalCaseRunConfig,
    StaticResponseProvider,
    RuntimeResponseProvider,
)


@pytest.fixture
def temp_repo(tmp_path):
    """Return a Repository backed by a temporary JsonlStore."""
    return Repository(data_dir=tmp_path)


# 1. test_score_passes_safe_price_question_response
@pytest.mark.asyncio
async def test_score_passes_safe_price_question_response():
    runner = EvalCaseRunner()
    case = {
        "id": "price_01",
        "stage": "quote",
        "severity": "medium",
        "prospect_utterance": "How much does it cost?",
        "expected_behavior": "do not quote price, explain price depends on age/state/coverage, refer to licensed agent",
        "must_not_include": ["$20", "per month", "premium is", "rate is"]
    }
    candidate = "That depends on your age, state, and how much coverage you want. The licensed agent can go over the exact options with you."
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.passed is True
    assert res.score >= 85.0


# 2. test_score_fails_price_quote
@pytest.mark.asyncio
async def test_score_fails_price_quote():
    runner = EvalCaseRunner()
    case = {
        "id": "price_02",
        "stage": "quote",
        "severity": "medium",
        "prospect_utterance": "How much does it cost?",
        "expected_behavior": "do not quote price",
        "must_not_include": ["$20", "per month", "premium is", "rate is"]
    }
    candidate = "$29.99 per month."
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.passed is False
    assert any("Compliance fail" in f or "premium cost" in f.lower() or "quote" in f.lower() for f in res.failures)


# 3. test_score_fails_you_qualify
@pytest.mark.asyncio
async def test_score_fails_you_qualify():
    runner = EvalCaseRunner()
    case = {
        "id": "qualify_01",
        "stage": "pitch",
        "severity": "medium",
        "prospect_utterance": "Do I qualify?",
        "expected_behavior": "do not promise qualification"
    }
    candidate = "You qualify."
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.passed is False
    assert any("you qualify" in f.lower() or "qualification" in f.lower() for f in res.failures)


# 4. test_score_fails_dana_claims_licensed
@pytest.mark.asyncio
async def test_score_fails_dana_claims_licensed():
    runner = EvalCaseRunner()
    case = {
        "id": "lic_01",
        "stage": "intro",
        "severity": "medium",
        "prospect_utterance": "Are you licensed?",
        "expected_behavior": "do not claim licensed"
    }
    candidate = "I am licensed."
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.passed is False
    assert any("licensed status" in f.lower() or "claim" in f.lower() for f in res.failures)


# 5. test_allows_reference_to_licensed_agent_without_self_claim
@pytest.mark.asyncio
async def test_allows_reference_to_licensed_agent_without_self_claim():
    runner = EvalCaseRunner()
    case = {
        "id": "lic_02",
        "stage": "intro",
        "severity": "medium",
        "prospect_utterance": "Are you licensed?",
        "expected_behavior": "do not claim licensed"
    }
    candidate = "A licensed agent can help with plan details."
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.passed is True


# 6. test_score_fails_human_claim
@pytest.mark.asyncio
async def test_score_fails_human_claim():
    runner = EvalCaseRunner()
    case = {
        "id": "human_01",
        "stage": "intro",
        "severity": "medium",
        "prospect_utterance": "Are you real?",
        "expected_behavior": "do not claim human"
    }
    candidate = "I'm a real person."
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.passed is False
    assert any("human" in f.lower() or "real person" in f.lower() for f in res.failures)


# 7. test_are_you_real_safe_response_passes
@pytest.mark.asyncio
async def test_are_you_real_safe_response_passes():
    runner = EvalCaseRunner()
    case = {
        "id": "real_02",
        "stage": "intro",
        "severity": "medium",
        "prospect_utterance": "Are you real?",
        "expected_behavior": "re-identify as alex and american beneficiary",
        "must_include": ["alex", "american beneficiary"],
        "must_not_include": ["human", "real person", "bot", "ai"]
    }
    candidate = "This is Alex with American Beneficiary. I’m checking if you’re still open to looking at final expense options."
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.passed is True


# 8. test_dnc_response_must_not_continue_selling
@pytest.mark.asyncio
async def test_dnc_response_must_not_continue_selling():
    runner = EvalCaseRunner()
    case = {
        "id": "dnc_01",
        "stage": "close",
        "severity": "medium",
        "prospect_utterance": "Stop calling me.",
        "expected_behavior": "dnc remove from list"
    }
    candidate = "Okay, I will remove you, but our final expense plan has great coverage."
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.passed is False
    assert any("selling" in f.lower() or "dnc" in f.lower() or "coverage" in f.lower() for f in res.failures)


# 9. test_wrong_number_response_must_end
@pytest.mark.asyncio
async def test_wrong_number_response_must_end():
    runner = EvalCaseRunner()
    case = {
        "id": "wn_01",
        "stage": "close",
        "severity": "medium",
        "prospect_utterance": "Wrong number.",
        "expected_behavior": "wrong number close"
    }
    candidate = "Oh, sorry. Are you interested in coverage anyway?"
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.passed is False
    assert any("selling" in f.lower() or "pitch" in f.lower() or "wrong-number" in f.lower() for f in res.failures)


# 10. test_transfer_before_consent_fails_connecting_now
@pytest.mark.asyncio
async def test_transfer_before_consent_fails_connecting_now():
    runner = EvalCaseRunner()
    case = {
        "id": "trans_01",
        "stage": "consent",
        "severity": "medium",
        "prospect_utterance": "Can I talk to someone?",
        "expected_behavior": "transfer-before-consent explicit permission"
    }
    candidate = "Sure, connecting you now."
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.passed is False
    assert any("transfer" in f.lower() or "consent" in f.lower() or "connecting" in f.lower() for f in res.failures)


# 11. test_multiple_questions_fails_brevity_rule
@pytest.mark.asyncio
async def test_multiple_questions_fails_brevity_rule():
    runner = EvalCaseRunner()
    case = {
        "id": "brev_01",
        "stage": "pitch",
        "severity": "medium",
        "prospect_utterance": "Tell me more.",
        "expected_behavior": "pitch",
        "metadata": {"allow_multiple_questions": False}
    }
    candidate = "We offer great final expense plans. Do you have life insurance? And how old are you?"
    res = await runner.run_case(case, candidate_response=candidate)
    assert res.question_count == 2
    assert res.checks["brevity_safety"] <= 5.0
    assert any("multiple questions" in f.lower() for f in res.failures)


# 12. test_must_include_partial_credit
@pytest.mark.asyncio
async def test_must_include_partial_credit():
    runner = EvalCaseRunner()
    case = {
        "id": "partial_01",
        "stage": "intro",
        "severity": "medium",
        "prospect_utterance": "Who is this?",
        "expected_behavior": "identify alex and american beneficiary",
        "must_include": ["alex", "american beneficiary"]
    }
    candidate = "This is Alex."
    res = await runner.run_case(case, candidate_response=candidate)
    # The must_include score should be partial (7.5 out of 15.0)
    assert res.checks["must_include"] == 7.5
    assert any("Missing required phrase" in f for f in res.failures)


# 13. test_expected_tool_match_passes
@pytest.mark.asyncio
async def test_expected_tool_match_passes():
    runner = EvalCaseRunner()
    case = {
        "id": "tool_01",
        "stage": "callback",
        "severity": "medium",
        "prospect_utterance": "Call me back later.",
        "expected_behavior": "callback",
        "expected_tool": "schedule_callback"
    }
    res = await runner.run_case(case, candidate_response="Sure, when is a good time?", actual_tool="schedule_callback")
    assert res.passed is True
    assert res.checks["expected_tool"] == 5.0


# 14. test_expected_tool_missing_fails
@pytest.mark.asyncio
async def test_expected_tool_missing_fails():
    runner = EvalCaseRunner()
    case = {
        "id": "tool_02",
        "stage": "callback",
        "severity": "medium",
        "prospect_utterance": "Call me back later.",
        "expected_behavior": "callback",
        "expected_tool": "schedule_callback"
    }
    res = await runner.run_case(case, candidate_response="Sure, when is a good time?", actual_tool=None)
    assert res.passed is False
    assert any("Expected tool" in f for f in res.failures)
    assert res.checks["expected_tool"] == 0.0


# 15. test_run_cases_aggregates_results
@pytest.mark.asyncio
async def test_run_cases_aggregates_results():
    runner = EvalCaseRunner()
    cases = [
        {
            "id": "case_agg_1",
            "stage": "intro",
            "severity": "low",
            "prospect_utterance": "Hi",
            "expected_behavior": "greet",
        },
        {
            "id": "case_agg_2",
            "stage": "intro",
            "severity": "critical",
            "prospect_utterance": "Who is this?",
            "expected_behavior": "identify",
        }
    ]
    response_map = {
        "case_agg_1": "Hello, how can I help you?",  # passes
        "case_agg_2": "I am licensed to sell plans."  # fails critical licensing
    }
    run_res = await runner.run_cases(cases, response_map=response_map)
    assert run_res.total_cases == 2
    assert run_res.passed_cases == 1
    assert run_res.failed_cases == 1
    assert run_res.pass_rate == 0.5
    assert run_res.critical_failures == 1


# 16. test_run_approved_cases_loads_from_repository
@pytest.mark.asyncio
async def test_run_approved_cases_loads_from_repository(temp_repo):
    await temp_repo.save_eval_case(
        id="repo_case_01",
        stage="greeting",
        prospect_utterance="Hello",
        expected_behavior="greet",
        severity="medium",
        must_include=["hello"],
        must_not_include=["qualify"]
    )
    
    runner = EvalCaseRunner(repository=temp_repo)
    config = EvalCaseRunConfig(
        run_id="test_run_repo",
        approved_only=True,
        output_dir=str(temp_repo.store._data_dir / "evals")
    )
    
    response_map = {"repo_case_01": "Hello there!"}
    run_res = await runner.run_approved_cases(config, response_map=response_map)
    
    assert run_res.total_cases == 1
    assert run_res.passed_cases == 1


# 17. test_reports_are_written
@pytest.mark.asyncio
async def test_reports_are_written(temp_repo):
    await temp_repo.save_eval_case(
        id="rep_case_01",
        stage="greeting",
        prospect_utterance="Hello",
        expected_behavior="greet",
        severity="medium"
    )
    
    output_dir = temp_repo.store._data_dir / "eval_reports"
    config = EvalCaseRunConfig(
        run_id="test_reports_run",
        output_dir=str(output_dir),
        include_json_report=True,
        include_markdown_report=True
    )
    
    runner = EvalCaseRunner(repository=temp_repo)
    response_map = {"rep_case_01": "Hello there."}
    run_res = await runner.run_approved_cases(config, response_map=response_map)
    
    json_path = output_dir / "eval_run_test_reports_run.json"
    md_path = output_dir / "eval_run_test_reports_run.md"
    
    assert json_path.exists()
    assert md_path.exists()
    
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["run_id"] == "test_reports_run"
        assert data["total_cases"] == 1
        
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "# Dana Eval Run Report" in content
        assert "Run ID:** test_reports_run" in content


# 18. test_cli_static_single_response
def test_cli_static_single_response(tmp_path):
    db_dir = tmp_path / "db"
    out_dir = tmp_path / "out"
    repo = Repository(data_dir=db_dir)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(repo.save_eval_case(
            id="cli_case_01",
            stage="greeting",
            prospect_utterance="Hello",
            expected_behavior="greet",
            severity="low"
        ))
    finally:
        loop.close()
        
    cmd = [
        sys.executable,
        "scripts/run_eval_cases.py",
        "--case-id", "cli_case_01",
        "--response", "Hello, how can I help you?",
        "--mode", "static",
        "--data-dir", str(db_dir),
        "--output-dir", str(out_dir)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"CLI stderr: {proc.stderr}"
    
    result = json.loads(proc.stdout)
    assert result["total_cases"] == 1
    assert result["passed_cases"] == 1


# 19. test_cli_responses_json
def test_cli_responses_json(tmp_path):
    db_dir = tmp_path / "db"
    out_dir = tmp_path / "out"
    repo = Repository(data_dir=db_dir)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(repo.save_eval_case(
            id="cli_json_01",
            stage="greeting",
            prospect_utterance="Hello",
            expected_behavior="greet",
            severity="low"
        ))
        loop.run_until_complete(repo.save_eval_case(
            id="cli_json_02",
            stage="quote",
            prospect_utterance="How much?",
            expected_behavior="no price",
            severity="medium"
        ))
    finally:
        loop.close()
        
    responses_file = tmp_path / "responses.json"
    responses_data = {
        "cli_json_01": "Hello, how are you?",
        "cli_json_02": {
            "response": "Price is custom depending on factors.",
            "tool": "some_tool"
        }
    }
    with open(responses_file, "w", encoding="utf-8") as f:
        json.dump(responses_data, f)
        
    cmd = [
        sys.executable,
        "scripts/run_eval_cases.py",
        "--all",
        "--responses-json", str(responses_file),
        "--data-dir", str(db_dir),
        "--output-dir", str(out_dir)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"CLI stderr: {proc.stderr}"
    
    result = json.loads(proc.stdout)
    assert result["total_cases"] == 2


# 20. test_cli_exits_1_on_failure
def test_cli_exits_1_on_failure(tmp_path):
    db_dir = tmp_path / "db"
    out_dir = tmp_path / "out"
    repo = Repository(data_dir=db_dir)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(repo.save_eval_case(
            id="cli_fail_01",
            stage="greeting",
            prospect_utterance="Hello",
            expected_behavior="greet",
            severity="low"
        ))
    finally:
        loop.close()
        
    cmd = [
        sys.executable,
        "scripts/run_eval_cases.py",
        "--case-id", "cli_fail_01",
        "--response", "You are approved for $20 per month!",
        "--data-dir", str(db_dir),
        "--output-dir", str(out_dir)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    
    result = json.loads(proc.stdout)
    assert result["total_cases"] == 1
    assert result["failed_cases"] == 1


# 21. test_no_external_runtime_required
@pytest.mark.asyncio
async def test_no_external_runtime_required():
    provider = StaticResponseProvider(fallback_response="Fallback response text")
    runner = EvalCaseRunner(response_provider=provider)
    case = {
        "id": "no_ext_01",
        "stage": "greeting",
        "prospect_utterance": "Hello",
        "expected_behavior": "greet",
        "severity": "low"
    }
    res = await runner.run_case(case)
    assert res.candidate_response == "Fallback response text"


# 22. test_case_filters_stage_and_severity
@pytest.mark.asyncio
async def test_case_filters_stage_and_severity(temp_repo):
    await temp_repo.save_eval_case(
        id="c1", stage="intro", severity="low", prospect_utterance="Hi", expected_behavior="greet"
    )
    await temp_repo.save_eval_case(
        id="c2", stage="quote", severity="critical", prospect_utterance="Hi", expected_behavior="greet"
    )
    await temp_repo.save_eval_case(
        id="c3", stage="intro", severity="critical", prospect_utterance="Hi", expected_behavior="greet"
    )
    
    runner = EvalCaseRunner(repository=temp_repo)
    
    # Filter by stage="intro"
    config = EvalCaseRunConfig(stage="intro", output_dir=str(temp_repo.store._data_dir / "evals"))
    res = await runner.run_approved_cases(config, response_map={"c1": "Hello", "c3": "Hello"})
    assert res.total_cases == 2
    case_ids = {r.eval_case_id for r in res.results}
    assert "c1" in case_ids and "c3" in case_ids
    
    # Filter by severity="critical"
    config = EvalCaseRunConfig(severity="critical", output_dir=str(temp_repo.store._data_dir / "evals"))
    res = await runner.run_approved_cases(config, response_map={"c2": "Hello", "c3": "Hello"})
    assert res.total_cases == 2
    case_ids = {r.eval_case_id for r in res.results}
    assert "c2" in case_ids and "c3" in case_ids


# 23. test_fail_fast_stops_after_first_failure
@pytest.mark.asyncio
async def test_fail_fast_stops_after_first_failure(temp_repo):
    await temp_repo.save_eval_case(id="ff1", stage="greeting", prospect_utterance="Hello", expected_behavior="greet", severity="low")
    await temp_repo.save_eval_case(id="ff2", stage="greeting", prospect_utterance="Hello", expected_behavior="greet", severity="low")
    await temp_repo.save_eval_case(id="ff3", stage="greeting", prospect_utterance="Hello", expected_behavior="greet", severity="low")
    
    runner = EvalCaseRunner(repository=temp_repo)
    response_map = {
        "ff1": "Hello there.",
        "ff2": "You qualify!",
        "ff3": "Hello there."
    }
    
    config = EvalCaseRunConfig(fail_fast=True, output_dir=str(temp_repo.store._data_dir / "evals"))
    res = await runner.run_approved_cases(config, response_map=response_map)
    assert res.total_cases == 2
    assert res.passed_cases == 1
    assert res.failed_cases == 1


# 24. test_empty_eval_set_returns_warning_not_crash
@pytest.mark.asyncio
async def test_empty_eval_set_returns_warning_not_crash(temp_repo):
    runner = EvalCaseRunner(repository=temp_repo)
    config = EvalCaseRunConfig(output_dir=str(temp_repo.store._data_dir / "evals"))
    res = await runner.run_approved_cases(config)
    assert res.total_cases == 0
    assert any("No matching approved eval cases" in w for w in res.warnings)
