"""Tests for Dana's Daily QA Miner."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

from storage.repository import Repository
from training.daily_qa_miner import DailyQaMiner, FailureCluster, WinningResponseCandidate, DailyQaMiningResult


@pytest.fixture
def temp_repo(tmp_path):
    """Return a Repository backed by a temporary JsonlStore."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def miner(temp_repo):
    """Return a DailyQaMiner using a temporary Repository."""
    return DailyQaMiner(repository=temp_repo)


@pytest.mark.asyncio
async def test_mine_date_handles_no_data(miner):
    """1. No calls exist. Should produce result with warnings, not crash."""
    result = await miner.mine_date("2026-05-30")
    assert isinstance(result, DailyQaMiningResult)
    assert result.total_calls_analyzed == 0
    assert result.total_turns_analyzed == 0
    # Should not crash and should complete successfully.


@pytest.mark.asyncio
async def test_detects_dnc_failure(miner, temp_repo):
    """2. Prospect says stop calling, agent continues. Creates compliance_review, failure_example, eval_case."""
    call_id = "call_dnc_001"
    # Save a call record
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    
    # Save turns
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="prospect",
        text="Stop calling me and remove me from your list",
        stage="greeting",
        created_at="2026-05-30T10:00:01Z"
    )
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=2,
        speaker="agent",
        text="I understand, but let me tell you about final expense options",
        stage="greeting",
        created_at="2026-05-30T10:00:05Z"
    )

    # We also trigger a dummy DNC tool event so that we don't get dnc_requested_no_tool
    await temp_repo.save_tool_event(
        call_id=call_id,
        tool_name="add_to_dnc",
        success=True,
        timestamp="2026-05-30T10:00:06Z"
    )

    result = await miner.mine_date("2026-05-30")
    assert result.human_review_items_created >= 3
    
    items = await temp_repo.list_recent_human_review_items(limit=10)
    types = [item["item_type"] for item in items]
    assert "compliance_review" in types
    assert "failure_example" in types
    assert "eval_case" in types

    comp_item = next(item for item in items if item["item_type"] == "compliance_review" and item["payload"].get("failure_type") == "continued_talking_after_dnc")
    assert comp_item["payload"]["failure_type"] == "continued_talking_after_dnc"
    assert comp_item["payload"]["severity"] == "critical"


@pytest.mark.asyncio
async def test_detects_wrong_number_failure(miner, temp_repo):
    """3. Prospect says wrong number, agent continues. Creates compliance_review and eval_case."""
    call_id = "call_wn_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="prospect",
        text="No, this is the wrong number, you have the wrong person",
        stage="greeting",
        created_at="2026-05-30T10:00:01Z"
    )
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=2,
        speaker="agent",
        text="Oh sorry, but are you interested in insurance options anyway?",
        stage="greeting",
        created_at="2026-05-30T10:00:05Z"
    )

    # Add dummy close event to avoid wrong_number_no_close_event matching first
    await temp_repo.save_tool_event(
        call_id=call_id,
        tool_name="close_call",
        success=True,
        timestamp="2026-05-30T10:00:06Z"
    )

    result = await miner.mine_date("2026-05-30")
    assert result.compliance_review_items_created >= 1
    assert result.eval_case_candidates_created >= 1

    items = await temp_repo.list_recent_human_review_items(limit=10)
    types = [item["item_type"] for item in items]
    assert "compliance_review" in types
    assert "eval_case" in types


@pytest.mark.asyncio
async def test_detects_transfer_before_consent(miner, temp_repo):
    """4. Agent uses transfer language before consent. Creates critical compliance_review and eval_case."""
    call_id = "call_tr_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="prospect",
        text="What is this about?",
        stage="pitch",
        created_at="2026-05-30T10:00:01Z"
    )
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=2,
        speaker="agent",
        text="Let me connect you now to a specialist who can help.",
        stage="pitch",
        created_at="2026-05-30T10:00:05Z"
    )

    # Set dummy outcome label so that it does not detect as hangup_after_agent_turn
    await temp_repo._store.save("call_outcome_labels", {
        "id": "tr_out_01",
        "call_id": call_id,
        "outcome": "transfer_successful",
        "created_at": "2026-05-30T10:00:06Z"
    })

    result = await miner.mine_date("2026-05-30")
    assert result.compliance_review_items_created >= 1
    
    items = await temp_repo.list_recent_human_review_items(limit=10)
    comp_item = next(item for item in items if item["item_type"] == "compliance_review" and item["payload"]["call_id"] == call_id)
    assert comp_item["payload"]["failure_type"] == "transfer_before_consent"
    assert comp_item["payload"]["severity"] == "critical"


@pytest.mark.asyncio
async def test_detects_agent_price_quote(miner, temp_repo):
    """5. Agent quotes price. Creates critical compliance_review, failure_example, eval_case."""
    call_id = "call_price_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="prospect",
        text="How much does it cost?",
        stage="pitch",
        created_at="2026-05-30T10:00:01Z"
    )
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=2,
        speaker="agent",
        text="It is only $29.99 per month for this coverage.",
        stage="pitch",
        created_at="2026-05-30T10:00:05Z"
    )

    # Set dummy outcome label so it is not a hangup failure
    await temp_repo._store.save("call_outcome_labels", {
        "id": "pr_out_01",
        "call_id": call_id,
        "outcome": "transfer_successful",
        "created_at": "2026-05-30T10:00:06Z"
    })

    result = await miner.mine_date("2026-05-30")
    assert result.compliance_review_items_created >= 1
    
    items = await temp_repo.list_recent_human_review_items(limit=10)
    types = [item["item_type"] for item in items if item["payload"].get("call_id") == call_id or call_id in item["payload"].get("supporting_call_ids", [])]
    assert "compliance_review" in types
    assert "failure_example" in types
    assert "eval_case" in types


@pytest.mark.asyncio
async def test_detects_agent_you_qualify(miner, temp_repo):
    """6. Agent says “you qualify.” Creates critical compliance_review."""
    call_id = "call_qual_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="agent",
        text="Based on your answers, you qualify for our guaranteed policy.",
        stage="pitch",
        created_at="2026-05-30T10:00:01Z"
    )

    # Set dummy outcome label so it is not a hangup failure
    await temp_repo._store.save("call_outcome_labels", {
        "id": "qual_out_01",
        "call_id": call_id,
        "outcome": "transfer_successful",
        "created_at": "2026-05-30T10:00:06Z"
    })

    result = await miner.mine_date("2026-05-30")
    assert result.compliance_review_items_created >= 1
    
    items = await temp_repo.list_recent_human_review_items(limit=10)
    comp_item = next(item for item in items if item["item_type"] == "compliance_review" and item["payload"]["call_id"] == call_id)
    assert comp_item["payload"]["failure_type"] == "agent_you_qualify"
    assert comp_item["payload"]["severity"] == "critical"


@pytest.mark.asyncio
async def test_detects_agent_claims_licensed(miner, temp_repo):
    """7. Agent says “I’m licensed.” Creates critical compliance_review."""
    call_id = "call_lic_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="agent",
        text="I am licensed to write policies in your state.",
        stage="pitch",
        created_at="2026-05-30T10:00:01Z"
    )

    # Set dummy outcome label so it is not a hangup failure
    await temp_repo._store.save("call_outcome_labels", {
        "id": "lic_out_01",
        "call_id": call_id,
        "outcome": "transfer_successful",
        "created_at": "2026-05-30T10:00:06Z"
    })

    result = await miner.mine_date("2026-05-30")
    assert result.compliance_review_items_created >= 1
    
    items = await temp_repo.list_recent_human_review_items(limit=10)
    comp_item = next(item for item in items if item["item_type"] == "compliance_review" and item["payload"]["call_id"] == call_id)
    assert comp_item["payload"]["failure_type"] == "agent_claims_licensed"
    assert comp_item["payload"]["severity"] == "critical"


@pytest.mark.asyncio
async def test_detects_agent_claims_human(miner, temp_repo):
    """8. Agent says “I’m a real person.” Creates critical compliance_review."""
    call_id = "call_hum_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="agent",
        text="I am a real person, not an AI bot.",
        stage="greeting",
        created_at="2026-05-30T10:00:01Z"
    )

    # Set dummy outcome label so it is not a hangup failure
    await temp_repo._store.save("call_outcome_labels", {
        "id": "hum_out_01",
        "call_id": call_id,
        "outcome": "transfer_successful",
        "created_at": "2026-05-30T10:00:06Z"
    })

    result = await miner.mine_date("2026-05-30")
    assert result.compliance_review_items_created >= 1
    
    items = await temp_repo.list_recent_human_review_items(limit=10)
    comp_item = next(item for item in items if item["item_type"] == "compliance_review" and item["payload"]["call_id"] == call_id)
    assert comp_item["payload"]["failure_type"] == "agent_claims_human"
    assert comp_item["payload"]["severity"] == "critical"


@pytest.mark.asyncio
async def test_detects_multiple_questions(miner, temp_repo):
    """9. Agent asks more than one question. Creates failure_example."""
    call_id = "call_mq_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="agent",
        text="What is your age? And what state do you reside in?",
        stage="pitch",
        created_at="2026-05-30T10:00:01Z"
    )

    # Set dummy outcome label so it is not a hangup failure
    await temp_repo._store.save("call_outcome_labels", {
        "id": "mq_out_01",
        "call_id": call_id,
        "outcome": "transfer_successful",
        "created_at": "2026-05-30T10:00:06Z"
    })

    result = await miner.mine_date("2026-05-30")
    assert result.eval_case_candidates_created >= 1
    
    items = await temp_repo.list_recent_human_review_items(limit=10)
    fail_item = next(item for item in items if item["item_type"] == "failure_example" and item["payload"]["call_id"] == call_id and item["payload"].get("failure_type") == "multiple_questions")
    assert fail_item["payload"]["failure_type"] == "multiple_questions"


@pytest.mark.asyncio
async def test_identifies_winning_response_candidate(miner, temp_repo):
    """10. Safe handling of price question followed by positive response. Creates training_example."""
    call_id = "call_win_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="prospect",
        text="How much does it cost?",
        stage="pitch",
        created_at="2026-05-30T10:00:01Z"
    )
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=2,
        speaker="agent",
        text="Pricing depends on customized options like age and state. May I ask your age first?",
        stage="pitch",
        created_at="2026-05-30T10:00:05Z"
    )
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=3,
        speaker="prospect",
        text="Yes, I am 62.",
        stage="pitch",
        created_at="2026-05-30T10:00:10Z"
    )

    result = await miner.mine_date("2026-05-30")
    assert result.winning_response_candidates_created >= 1
    
    items = await temp_repo.list_recent_human_review_items(limit=10)
    win_item = next(item for item in items if item["item_type"] == "training_example")
    assert win_item["payload"]["objection_type"] == "price"
    assert win_item["payload"]["user_text"] == "How much does it cost?"


@pytest.mark.asyncio
async def test_creates_eval_case_for_callback_failure(miner, temp_repo):
    """11. Prospect asks callback, but no callback tool event. Creates eval_case."""
    call_id = "call_cb_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="prospect",
        text="Please call me back tomorrow afternoon.",
        stage="pitch",
        created_at="2026-05-30T10:00:01Z"
    )

    result = await miner.mine_date("2026-05-30")
    assert result.eval_case_candidates_created >= 1
    
    items = await temp_repo.list_recent_human_review_items(limit=10)
    eval_item = next(item for item in items if item["item_type"] == "eval_case" and call_id in item["payload"]["supporting_call_ids"])
    assert eval_item["payload"]["failure_type"] == "callback_requested_no_tool"


@pytest.mark.asyncio
async def test_dedupes_review_items(miner, temp_repo):
    """12. Run miner twice on same data. Second run creates zero new review items."""
    call_id = "call_dedup_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="agent",
        text="What is your age? And what state are you in?",
        stage="pitch",
        created_at="2026-05-30T10:00:01Z"
    )

    # First run
    result1 = await miner.mine_date("2026-05-30")
    items1 = result1.human_review_items_created
    assert items1 > 0

    # Second run
    result2 = await miner.mine_date("2026-05-30")
    assert result2.human_review_items_created == 0
    assert result2.skipped_items == items1


@pytest.mark.asyncio
async def test_dry_run_does_not_create_review_items(miner, temp_repo):
    """13. Run with dry_run. Report generated but no HumanReviewItems created."""
    call_id = "call_dry_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="agent",
        text="I am licensed in your state.",
        stage="pitch",
        created_at="2026-05-30T10:00:01Z"
    )

    result = await miner.mine_date("2026-05-30", dry_run=True)
    assert result.human_review_items_created > 0  # reported count
    
    # Check DB
    db_items = await temp_repo.list_recent_human_review_items(limit=10)
    assert len(db_items) == 0


@pytest.mark.asyncio
async def test_report_files_are_written(miner, temp_repo, tmp_path):
    """14. Assert markdown and JSON report files exist and markdown has required sections."""
    call_id = "call_rep_001"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    await temp_repo.save_call_turn(
        call_id=call_id,
        turn_number=1,
        speaker="agent",
        text="Guaranteed approval status.",
        stage="pitch",
        created_at="2026-05-30T10:00:01Z"
    )

    # Inject custom report writer path
    reports_dir = tmp_path / "reports"
    
    # Call mine
    result = await miner.mine_range("2026-05-30", "2026-05-30")
    
    # We must call write_daily_report manually with custom path to verify
    md_path, json_path = miner.write_daily_report(result, result, output_dir=str(reports_dir))
    
    assert os.path.exists(md_path)
    assert os.path.exists(json_path)

    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "# Dana Daily QA Mining Report" in content
        assert "## Executive Summary" in content
        assert "## Compliance Alerts" in content
        assert "## Top Failure Clusters" in content


def test_cli_date_run_outputs_json(tmp_path):
    """15. Run CLI subprocess and assert stdout is JSON and exit code 0."""
    data_dir = tmp_path / "data"
    reports_dir = tmp_path / "reports"
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    # Initialize Repository to write basic DB metadata so that CLI runs correctly
    repo = Repository(data_dir=data_dir)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(repo.save_call(call_id="cli_call_01", started_at="2026-05-30T10:00:00Z"))
    finally:
        loop.close()

    # Invoke CLI
    cmd = [
        sys.executable,
        "scripts/run_daily_qa_miner.py",
        "--date", "2026-05-30",
        "--data-dir", str(data_dir),
        "--output-dir", str(reports_dir)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"CLI failed: {proc.stderr}"
    
    # Stdout must be clean JSON
    data = json.loads(proc.stdout)
    assert data["date_from"] == "2026-05-30"
    assert data["total_calls_analyzed"] >= 1


def test_cli_dry_run_does_not_create_items(tmp_path):
    """16. Run CLI with dry-run and verify no database entries are created."""
    data_dir = tmp_path / "data"
    reports_dir = tmp_path / "reports"
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    # Save a failure turn
    repo = Repository(data_dir=data_dir)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(repo.save_call(call_id="cli_dry_01", started_at="2026-05-30T10:00:00Z"))
        loop.run_until_complete(repo.save_call_turn(
            call_id="cli_dry_01",
            turn_number=1,
            speaker="agent",
            text="I am licensed to sell final expense plans.",
            stage="greeting",
            created_at="2026-05-30T10:00:01Z"
        ))
    finally:
        loop.close()

    # Run CLI
    cmd = [
        sys.executable,
        "scripts/run_daily_qa_miner.py",
        "--date", "2026-05-30",
        "--dry-run",
        "--data-dir", str(data_dir),
        "--output-dir", str(reports_dir)
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"CLI failed: {proc.stderr}"

    # Verify no HumanReviewItems in DB
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        items = loop.run_until_complete(repo.list_recent_human_review_items(limit=10))
        assert len(items) == 0
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_handles_missing_repository_helpers_gracefully(tmp_path):
    """17. Mock repository that lacks query helpers. Miner should warn but not crash."""
    class BadRepo:
        def __init__(self):
            # Lacks query_calls, etc.
            pass

    miner_bad = DailyQaMiner(repository=BadRepo())
    result = await miner_bad.mine_date("2026-05-30")
    assert isinstance(result, DailyQaMiningResult)
    assert len(result.warnings) > 0
    assert any("query_calls method missing" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_failure_clusters_include_severity_and_recommendations(miner, temp_repo):
    """18. Assert FailureCluster severity and recommended_action are populated."""
    failures = [
        {
            "call_id": "call_c_01",
            "turn_index": 2,
            "stage": "pitch",
            "objection_type": None,
            "failure_type": "agent_price_quote",
            "severity": "critical",
            "user_text": "price?",
            "agent_response": "20 dollars",
            "details": "Price quoted."
        }
    ]
    clusters = miner.cluster_failures(failures)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.severity == "critical"
    assert "Enforce no price quoting" in c.recommended_action
    assert c.count == 1
    assert "call_c_01" in c.sample_call_ids


@pytest.mark.asyncio
async def test_objection_frequency_summary(miner, temp_repo):
    """19. Assert objection frequency appears in result and JSON report."""
    call_id = "call_obj_01"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    
    # Save the turn directly to bypass schema stripping
    await temp_repo._store.save("call_turns", {
        "id": "turn_obj_01",
        "call_id": call_id,
        "turn_number": 1,
        "speaker": "prospect",
        "text": "I am already insured",
        "stage": "pitch",
        "objection_type": "already_insured",
        "timestamp": "2026-05-30T10:00:01Z"
    })

    result = await miner.mine_date("2026-05-30")
    assert "already_insured" in result.objection_frequency
    assert result.objection_frequency["already_insured"] >= 1


@pytest.mark.asyncio
async def test_hangups_by_stage_summary(miner, temp_repo):
    """20. Assert hangups_by_stage summary appears in result."""
    call_id = "call_hang_01"
    await temp_repo.save_call(call_id=call_id, started_at="2026-05-30T10:00:00Z")
    
    # Save call outcome labels directly to bypass schema stripping
    await temp_repo._store.save("call_outcome_labels", {
        "id": "hang_outcome_01",
        "call_id": call_id,
        "outcome": "hangup",
        "labels": {"stage": "greeting"},
        "created_at": "2026-05-30T10:00:10Z"
    })

    result = await miner.mine_date("2026-05-30")
    assert "greeting" in result.hangups_by_stage
    assert result.hangups_by_stage["greeting"] >= 1
