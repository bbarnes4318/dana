"""Unit tests for the prompt canary rollout system (Prompt 16)."""

from __future__ import annotations

import os
import sys
import json
import hashlib
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import pytest
import asyncio

from storage.repository import Repository
from deployment.canary import CanaryManager, PromptResolver


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    """Return a Repository backed by a temporary JSONL store."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def manager(repo: Repository) -> CanaryManager:
    """Return a CanaryManager linked to the test Repository."""
    return CanaryManager(repository=repo)


def create_valid_candidate_payload(content: str) -> dict[str, Any]:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "file_path": "prompts/final_expense_alex.md",
        "sha": content_hash,
        "created_by": "test-user",
        "change_reason": "Rollout test candidate",
        "canary_status": "candidate",
        "qa_thresholds": {
            "content": content,
            "metadata": {
                "prompt_name": "final_expense_alex",
                "created_from": "prompt_patch_preview",
                "runtime_changed": False,
                "active_runtime": False,
                "source_prompt_hash": "stale_or_unstale_source_hash",
                "patched_prompt_hash": content_hash,
                "patch_review_item_ids": ["rev-123"],
                "gate_result": {
                    "passed": True,
                    "prompt_validation_passed": True,
                    "transcript_replay_passed": True,
                    "prospect_simulations_passed": True,
                    "eval_cases_passed": True,
                    "eval_cases_present": True,
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Eligibility Tests (Tests 1 to 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_eligibility_passes_for_valid_candidate(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    res = await manager.check_candidate_eligibility(pv_id)
    assert res.eligible is True
    assert not res.failures
    assert res.candidate_prompt_version_id == pv_id
    assert res.prompt_name == "final_expense_alex"


@pytest.mark.asyncio
async def test_candidate_eligibility_fails_missing_gates(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    # Remove gate result
    payload["qa_thresholds"]["metadata"].pop("gate_result")
    pv_id = await repo.save_prompt_version(**payload)

    res = await manager.check_candidate_eligibility(pv_id)
    assert res.eligible is False
    assert any("Gate result metadata is missing" in f for f in res.failures)


@pytest.mark.asyncio
async def test_candidate_eligibility_fails_failed_replay_gate(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    # Fail the transcript replay gate
    payload["qa_thresholds"]["metadata"]["gate_result"]["transcript_replay_passed"] = False
    pv_id = await repo.save_prompt_version(**payload)

    res = await manager.check_candidate_eligibility(pv_id)
    assert res.eligible is False
    assert any("transcript_replay_passed" in f for f in res.failures)


@pytest.mark.asyncio
async def test_candidate_eligibility_fails_status_not_candidate(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    payload["canary_status"] = "active"  # Anything but candidate
    pv_id = await repo.save_prompt_version(**payload)

    res = await manager.check_candidate_eligibility(pv_id)
    assert res.eligible is False
    assert any("canary_status is 'active'" in f for f in res.failures)


@pytest.mark.asyncio
async def test_candidate_eligibility_fails_hash_mismatch(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    # Manually spoil the hash
    payload["qa_thresholds"]["metadata"]["patched_prompt_hash"] = "wrong_hash_here"
    pv_id = await repo.save_prompt_version(**payload)

    res = await manager.check_candidate_eligibility(pv_id)
    assert res.eligible is False
    assert any("Content hash" in f and "does not match" in f for f in res.failures)


# ---------------------------------------------------------------------------
# Canary Plan Creation & Traffic Rules (Tests 6 & 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_canary_plan_creates_planned_experiment(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Test Canary Exp 1",
        created_by="Jimmy",
        traffic_percentage=2.5,
        max_traffic_percentage=10.0,
    )
    assert plan.status == "planned"
    assert plan.traffic_percentage == 2.5
    assert plan.metadata.get("runtime_default_changed") is False

    exp = await repo.get_deployment_experiment(plan.experiment_id)
    assert exp is not None
    assert exp["status"] == "planned"
    assert exp["traffic_percent"] == 2.5


@pytest.mark.asyncio
async def test_create_canary_plan_rejects_traffic_over_max(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    with pytest.raises(ValueError, match="exceeds max configured limit"):
        await manager.create_canary_plan(
            candidate_prompt_version_id=pv_id,
            experiment_name="Test Canary Exp Over Limit",
            created_by="Jimmy",
            traffic_percentage=50.0,
            max_traffic_percentage=10.0,
        )


# ---------------------------------------------------------------------------
# Status Transition Rules (Tests 8 to 14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_canary_requires_notes(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Test Approve Notes Exp",
        created_by="Jimmy",
        traffic_percentage=2.0,
    )

    with pytest.raises(ValueError, match="Approval notes are required"):
        await manager.approve_canary(plan.experiment_id, approved_by="Jimmy", approval_notes="")


@pytest.mark.asyncio
async def test_start_requires_approved_status(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Test Start Status Exp",
        created_by="Jimmy",
        traffic_percentage=1.0,
    )

    with pytest.raises(ValueError, match="Cannot move experiment from status 'planned' to 'running'"):
        await manager.start_canary(plan.experiment_id, started_by="Jimmy")


@pytest.mark.asyncio
async def test_status_transitions_happy_path(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Happy Transition Path",
        created_by="Jimmy",
    )
    exp_id = plan.experiment_id

    # planned -> approved
    res = await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Looks good.")
    assert res.success is True
    assert res.new_status == "approved"

    # approved -> running
    res = await manager.start_canary(exp_id, started_by="Jimmy")
    assert res.success is True
    assert res.new_status == "running"

    # running -> completed
    res = await manager.complete_canary(exp_id, completed_by="Jimmy", reason="Completed on schedule.")
    assert res.success is True
    assert res.new_status == "completed"

    exp = await repo.get_deployment_experiment(exp_id)
    audit = exp["metrics"]["audit_history"]
    assert len(audit) == 4  # create, approve, start, complete
    assert audit[-1]["operation"] == "transition_to_completed"


@pytest.mark.asyncio
async def test_pause_and_resume_running_canary(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Pause Resume Exp",
        created_by="Jimmy",
    )
    exp_id = plan.experiment_id

    await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Approved.")
    await manager.start_canary(exp_id, started_by="Jimmy")

    # running -> paused
    res = await manager.pause_canary(exp_id, paused_by="Jimmy", reason="Check compliance logs")
    assert res.new_status == "paused"

    # paused -> running
    res = await manager.start_canary(exp_id, started_by="Jimmy")
    assert res.new_status == "running"


@pytest.mark.asyncio
async def test_rollback_running_canary(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Rollback Exp",
        created_by="Jimmy",
    )
    exp_id = plan.experiment_id

    await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Approved.")
    await manager.start_canary(exp_id, started_by="Jimmy")

    # running -> rolled_back
    res = await manager.rollback_canary(exp_id, rolled_back_by="Jimmy", reason="High compliance violation count")
    assert res.new_status == "rolled_back"

    exp = await repo.get_deployment_experiment(exp_id)
    assert exp["status"] == "rolled_back"
    assert exp["metrics"]["rollback_reason"] == "High compliance violation count"


@pytest.mark.asyncio
async def test_cannot_restart_rolled_back_canary(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Rollback Resurrect",
        created_by="Jimmy",
    )
    exp_id = plan.experiment_id

    await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Approved.")
    await manager.start_canary(exp_id, started_by="Jimmy")
    await manager.rollback_canary(exp_id, rolled_back_by="Jimmy", reason="Fatal bug")

    # Try rolled_back -> running (should fail)
    with pytest.raises(ValueError, match="Cannot move experiment from status 'rolled_back'"):
        await manager.start_canary(exp_id, started_by="Jimmy")


@pytest.mark.asyncio
async def test_cancel_planned_canary(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Cancel Planned Exp",
        created_by="Jimmy",
    )
    exp_id = plan.experiment_id

    res = await manager.cancel_canary(exp_id, cancelled_by="Jimmy", reason="No longer required")
    assert res.new_status == "cancelled"

    exp = await repo.get_deployment_experiment(exp_id)
    assert exp["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Routing & Bucketing Rules (Tests 15 to 20)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_returns_control_when_env_disabled(
    repo: Repository, manager: CanaryManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DANA_ENABLE_PROMPT_CANARY", raising=False)

    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Disabled Routing Exp",
        created_by="Jimmy",
        traffic_percentage=10.0,
    )
    exp_id = plan.experiment_id
    await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Approved.")
    await manager.start_canary(exp_id, started_by="Jimmy")

    decision = await manager.choose_prompt_for_call(
        prompt_name="final_expense_alex",
        call_id="call-456",
        experiment_id=exp_id,
    )
    assert decision.use_candidate is False
    assert decision.reason == "canary routing disabled by environment"


@pytest.mark.asyncio
async def test_decision_uses_candidate_when_bucket_under_traffic(
    repo: Repository, manager: CanaryManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DANA_ENABLE_PROMPT_CANARY", "true")

    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="100% Canary Exp",
        created_by="Jimmy",
        traffic_percentage=100.0,
        max_traffic_percentage=100.0,
    )
    exp_id = plan.experiment_id
    await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Approved.")
    await manager.start_canary(exp_id, started_by="Jimmy")

    decision = await manager.choose_prompt_for_call(
        prompt_name="final_expense_alex",
        call_id="call-any",
        experiment_id=exp_id,
    )
    assert decision.use_candidate is True
    assert decision.prompt_version_id == pv_id
    assert "under traffic limit" in decision.reason


@pytest.mark.asyncio
async def test_decision_is_deterministic_for_same_call_id(
    repo: Repository, manager: CanaryManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DANA_ENABLE_PROMPT_CANARY", "true")

    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Deterministic Bucket Exp",
        created_by="Jimmy",
        traffic_percentage=5.0,
        max_traffic_percentage=10.0,
    )
    exp_id = plan.experiment_id
    await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Approved.")
    await manager.start_canary(exp_id, started_by="Jimmy")

    # Run routing choice multiple times on same inputs
    decisions = []
    for _ in range(10):
        d = await manager.choose_prompt_for_call(
            prompt_name="final_expense_alex",
            call_id="call-deterministic-id",
            experiment_id=exp_id,
        )
        decisions.append(d.use_candidate)

    # All decisions must be identical
    assert len(set(decisions)) == 1


@pytest.mark.asyncio
async def test_decision_force_control_always_control(
    repo: Repository, manager: CanaryManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DANA_ENABLE_PROMPT_CANARY", "true")

    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Force Control Exp",
        created_by="Jimmy",
        traffic_percentage=100.0,
        max_traffic_percentage=100.0,
    )
    exp_id = plan.experiment_id
    await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Approved.")
    await manager.start_canary(exp_id, started_by="Jimmy")

    decision = await manager.choose_prompt_for_call(
        prompt_name="final_expense_alex",
        call_id="call-1",
        experiment_id=exp_id,
        force_control=True,
    )
    assert decision.use_candidate is False
    assert decision.reason == "Force control requested"


@pytest.mark.asyncio
async def test_force_candidate_requires_env_flag(
    repo: Repository, manager: CanaryManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DANA_ENABLE_PROMPT_CANARY", "true")

    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Force Candidate Exp",
        created_by="Jimmy",
        traffic_percentage=0.01,
        max_traffic_percentage=10.0,
    )
    exp_id = plan.experiment_id
    await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Approved.")
    await manager.start_canary(exp_id, started_by="Jimmy")

    # 1. Unset force flag env
    monkeypatch.delenv("DANA_ALLOW_FORCE_CANDIDATE_PROMPT", raising=False)
    decision = await manager.choose_prompt_for_call(
        prompt_name="final_expense_alex",
        call_id="call-force",
        experiment_id=exp_id,
        force_candidate=True,
    )
    assert decision.use_candidate is False
    assert "disallowed by environment" in decision.reason

    # 2. Set force flag env
    monkeypatch.setenv("DANA_ALLOW_FORCE_CANDIDATE_PROMPT", "true")
    decision = await manager.choose_prompt_for_call(
        prompt_name="final_expense_alex",
        call_id="call-force",
        experiment_id=exp_id,
        force_candidate=True,
    )
    assert decision.use_candidate is True
    assert decision.prompt_version_id == pv_id
    assert decision.reason == "Force candidate requested"


@pytest.mark.asyncio
async def test_no_running_experiment_returns_control(repo: Repository, manager: CanaryManager) -> None:
    decision = await manager.choose_prompt_for_call(
        prompt_name="final_expense_alex",
        call_id="call-no-exp",
    )
    assert decision.use_candidate is False
    assert decision.reason == "No running canary experiment found"


# ---------------------------------------------------------------------------
# Reports (Test 21)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_canary_report_writes_json_and_markdown(
    repo: Repository, manager: CanaryManager, tmp_path: Path
) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(
        candidate_prompt_version_id=pv_id,
        experiment_name="Report Generation Exp",
        created_by="Jimmy",
    )
    exp_id = plan.experiment_id

    await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Notes.")
    await manager.start_canary(exp_id, started_by="Jimmy")

    json_path, md_path = await manager.generate_canary_report(exp_id, output_dir=tmp_path)

    assert Path(json_path).exists()
    assert Path(md_path).exists()

    # Read json report
    rep_data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    assert rep_data["experiment_id"] == exp_id
    assert rep_data["prompt_name"] == "final_expense_alex"
    assert rep_data["status"] == "running"

    # Read md report
    md_text = Path(md_path).read_text(encoding="utf-8")
    assert "# Dana Canary Rollout Report" in md_text
    assert "Control prompt version" in md_text
    assert "Audit History" in md_text


# ---------------------------------------------------------------------------
# CLI Commands Subprocess Invocation (Tests 22 to 24)
# ---------------------------------------------------------------------------


def run_cli_subprocess(args: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    cli_path = Path(__file__).parent.parent / "scripts" / "manage_canary_rollout.py"
    cmd = [sys.executable, str(cli_path)] + args
    
    workspace_root = str(Path(__file__).parent.parent)
    if env is None:
        env = os.environ.copy()
    else:
        env = env.copy()
        
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = workspace_root + os.pathsep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = workspace_root

    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return res.returncode, res.stdout, res.stderr


def test_cli_check_candidate_outputs_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Set DATABASE_URL to a temp JSONL path to bypass default empty DB settings
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DANA_DATA_DIR", str(tmp_path))

    repo = Repository(data_dir=tmp_path)
    content = "Candidate prompt data."
    payload = create_valid_candidate_payload(content)
    pv_id = asyncio.run(repo.save_prompt_version(**payload))

    # Run CLI check
    env = os.environ.copy()
    env["DANA_DATA_DIR"] = str(tmp_path)
    code, stdout, stderr = run_cli_subprocess(["check", "--candidate-id", pv_id], env=env)

    assert code == 0, f"code={code}, stdout={stdout}, stderr={stderr}"
    assert not stderr.strip()
    data = json.loads(stdout)
    assert data["eligible"] is True
    assert data["candidate_prompt_version_id"] == pv_id


def test_cli_create_approve_start_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DANA_DATA_DIR", str(tmp_path))

    repo = Repository(data_dir=tmp_path)
    content = "Prompt candidate flow."
    payload = create_valid_candidate_payload(content)
    pv_id = asyncio.run(repo.save_prompt_version(**payload))

    env = os.environ.copy()
    env["DANA_DATA_DIR"] = str(tmp_path)

    # 1. CLI Create Plan
    code, stdout, stderr = run_cli_subprocess(
        ["create", "--candidate-id", pv_id, "--name", "CLI Exp 1", "--created-by", "TestCLI"], env=env
    )
    assert code == 0, f"code={code}, stdout={stdout}, stderr={stderr}"
    plan_data = json.loads(stdout)
    exp_id = plan_data["experiment_id"]
    assert plan_data["status"] == "planned"

    # 2. CLI Approve Canary
    code, stdout, stderr = run_cli_subprocess(
        ["approve", "--experiment-id", exp_id, "--approved-by", "ApproverCLI", "--notes", "Notes from CLI"], env=env
    )
    assert code == 0, f"code={code}, stdout={stdout}, stderr={stderr}"
    app_data = json.loads(stdout)
    assert app_data["new_status"] == "approved"

    # 3. CLI Start Canary
    code, stdout, stderr = run_cli_subprocess(
        ["start", "--experiment-id", exp_id, "--started-by", "StarterCLI"], env=env
    )
    assert code == 0, f"code={code}, stdout={stdout}, stderr={stderr}"
    start_data = json.loads(stdout)
    assert start_data["new_status"] == "running"


def test_cli_decide_outputs_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DANA_DATA_DIR", str(tmp_path))

    env = os.environ.copy()
    env["DANA_DATA_DIR"] = str(tmp_path)
    env["DANA_ENABLE_PROMPT_CANARY"] = "true"

    repo = Repository(data_dir=tmp_path)
    content = "Routing prompt contents."
    payload = create_valid_candidate_payload(content)
    pv_id = asyncio.run(repo.save_prompt_version(**payload))

    # Create & Start Exp
    manager = CanaryManager(repository=repo)
    plan = asyncio.run(manager.create_canary_plan(pv_id, "Decide CLI Exp", "CLIAuthor", 100.0, 100.0))
    asyncio.run(manager.approve_canary(plan.experiment_id, "Approver", "Approved"))
    asyncio.run(manager.start_canary(plan.experiment_id, "Starter"))

    # Run decide command
    code, stdout, stderr = run_cli_subprocess(
        ["decide", "--prompt-name", "final_expense_alex", "--call-id", "call-decide-cli", "--experiment-id", plan.experiment_id],
        env=env,
    )
    assert code == 0, f"code={code}, stdout={stdout}, stderr={stderr}"
    dec_data = json.loads(stdout)
    assert dec_data["use_candidate"] is True
    assert dec_data["prompt_version_id"] == pv_id


# ---------------------------------------------------------------------------
# Safeguard Verifications (Tests 25 to 27)
# ---------------------------------------------------------------------------


def test_no_live_prompt_file_modified(tmp_path: Path) -> None:
    live_prompt_path = Path("prompts/final_expense_alex.md")
    assert live_prompt_path.exists()
    original_content = live_prompt_path.read_text(encoding="utf-8")

    # Run check, create plan, start, etc.
    repo = Repository(data_dir=tmp_path)
    manager = CanaryManager(repository=repo)

    content = "New canary data."
    payload = create_valid_candidate_payload(content)
    pv_id = asyncio.run(repo.save_prompt_version(**payload))

    plan = asyncio.run(manager.create_canary_plan(pv_id, "Safeguard Verification Exp", "Tester", 1.0))
    asyncio.run(manager.approve_canary(plan.experiment_id, "Approver", "OK"))
    asyncio.run(manager.start_canary(plan.experiment_id, "Starter"))
    asyncio.run(manager.choose_prompt_for_call("final_expense_alex", "call-test", plan.experiment_id))

    # Assert content of live prompt file is completely unchanged
    current_content = live_prompt_path.read_text(encoding="utf-8")
    assert current_content == original_content


def test_default_runtime_behavior_unchanged_if_hook_added(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ensure canary routing is disabled by environment (default status)
    monkeypatch.delenv("DANA_ENABLE_PROMPT_CANARY", raising=False)

    resolver = PromptResolver(repository=repo)
    # Even if we pass force_candidate=True or running experiments exist, default env must fail closed to default (None)
    pv_id = asyncio.run(resolver.resolve_prompt_version_id("final_expense_alex", "call-xyz"))
    assert pv_id is None


@pytest.mark.asyncio
async def test_audit_history_appended_for_operations(repo: Repository, manager: CanaryManager) -> None:
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    plan = await manager.create_canary_plan(pv_id, "Audit History Exp", "Jimmy")
    exp_id = plan.experiment_id

    await manager.approve_canary(exp_id, approved_by="Jimmy", approval_notes="Approve notes")
    await manager.start_canary(exp_id, started_by="Jimmy")
    await manager.pause_canary(exp_id, paused_by="Jimmy", reason="Pause reason")
    await manager.rollback_canary(exp_id, rolled_back_by="Jimmy", reason="Rollback reason")

    exp = await manager.get_canary(exp_id)
    history = exp["metrics"]["audit_history"]
    assert len(history) == 5  # create, approve, start, pause, rollback
    assert history[0]["operation"] == "create_plan"
    assert history[1]["operation"] == "transition_to_approved"
    assert history[2]["operation"] == "transition_to_running"
    assert history[3]["operation"] == "transition_to_paused"
    assert history[4]["operation"] == "transition_to_rolled_back"


@pytest.mark.asyncio
async def test_candidate_eligibility_passes_for_prompt15_created_candidate(
    repo: Repository, manager: CanaryManager
) -> None:
    from prompts.versioning import PromptVersionManager
    pvm = PromptVersionManager(repository=repo)

    content = "This is a candidate prompt content."
    content_hash = pvm.compute_content_hash(content)

    metadata = {
        "source_prompt_path": "prompts/final_expense_alex.md",
        "source_prompt_hash": "some_source_hash",
        "patched_prompt_hash": content_hash,
        "patch_review_item_ids": ["patch1"],
        "gate_result": {
            "passed": True,
            "prompt_validation_passed": True,
            "eval_cases_passed": True,
            "eval_cases_present": True,
            "transcript_replay_passed": True,
            "prospect_simulations_passed": True
        },
        "created_from": "prompt_patch_preview",
        "runtime_changed": False,
        "active_runtime": False
    }

    # We use PromptVersionManager.create_prompt_version()
    # status="candidate"
    res_snapshot = await pvm.create_prompt_version(
        prompt_name="final_expense_alex",
        content=content,
        created_by="tester",
        status="candidate",
        source_file="prompts/final_expense_alex.md",
        metadata=metadata
    )

    # Now check eligibility using manager
    elig = await manager.check_candidate_eligibility(res_snapshot.prompt_version_id)
    assert elig.eligible is True
    assert not elig.failures


@pytest.mark.asyncio
async def test_candidate_eligibility_uses_normalized_hash(
    repo: Repository, manager: CanaryManager
) -> None:
    from prompts.versioning import PromptVersionManager
    pvm = PromptVersionManager(repository=repo)

    # Create content with CRLF and trailing spaces
    content_raw = "Line one with space   \r\nLine two \r\n"
    normalized_hash = pvm.compute_content_hash(content_raw)

    metadata = {
        "source_prompt_path": "prompts/final_expense_alex.md",
        "source_prompt_hash": "some_source_hash",
        "patched_prompt_hash": normalized_hash,
        "patch_review_item_ids": ["patch1"],
        "gate_result": {
            "passed": True,
            "prompt_validation_passed": True,
            "eval_cases_passed": True,
            "eval_cases_present": True,
            "transcript_replay_passed": True,
            "prospect_simulations_passed": True
        },
        "created_from": "prompt_patch_preview",
        "runtime_changed": False,
        "active_runtime": False
    }

    res_snapshot = await pvm.create_prompt_version(
        prompt_name="final_expense_alex",
        content=content_raw,
        created_by="tester",
        status="candidate",
        source_file="prompts/final_expense_alex.md",
        metadata=metadata
    )

    elig = await manager.check_candidate_eligibility(res_snapshot.prompt_version_id)
    assert elig.eligible is True
    assert not elig.failures


@pytest.mark.asyncio
async def test_candidate_eligibility_backward_compatible_with_outer_metadata(
    repo: Repository, manager: CanaryManager
) -> None:
    # Keep existing direct qa_thresholds["metadata"] shape.
    # Assert eligibility still passes.
    content = "This is valid prompt text."
    payload = create_valid_candidate_payload(content)
    pv_id = await repo.save_prompt_version(**payload)

    res = await manager.check_candidate_eligibility(pv_id)
    assert res.eligible is True
    assert not res.failures
