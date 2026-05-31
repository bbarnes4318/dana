"""Unit and integration tests for canary monitoring, safety checks, and promotion readiness (Prompt 17)."""

from __future__ import annotations

import os
import sys
import json
import tempfile
import asyncio
from pathlib import Path

import pytest

from storage.repository import Repository
from deployment.canary import CanaryManager, PromptResolver
from deployment.monitoring import (
    CanaryMonitor,
    CanaryMonitorConfig,
    CanaryVariantMetrics,
    CanarySafetySignal,
)


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    """Return a Repository backed by a temporary JSONL store."""
    return Repository(data_dir=tmp_path)


@pytest.fixture
def canary_manager(repo: Repository) -> CanaryManager:
    """Return a CanaryManager linked to the test Repository."""
    return CanaryManager(repository=repo)


@pytest.fixture
def monitor(repo: Repository, canary_manager: CanaryManager) -> CanaryMonitor:
    """Return a CanaryMonitor linked to the test Repository."""
    return CanaryMonitor(repository=repo, canary_manager=canary_manager)


async def setup_running_experiment(repo: Repository) -> str:
    """Helper to set up a valid running experiment with candidate and control versions."""
    # Save a candidate prompt version record
    pv_payload = {
        "id": "cand-pv-123",
        "file_path": "prompts/final_expense_alex.md",
        "sha": "cand_hash_val",
        "created_by": "test-user",
        "change_reason": "Testing",
        "canary_status": "candidate",
        "qa_thresholds": {
            "prompt_name": "final_expense_alex",
            "version": "1.0",
            "content": "Candidate content",
            "metadata": {
                "prompt_name": "final_expense_alex",
                "source_prompt_hash": "src_hash_val",
                "patched_prompt_hash": "cand_hash_val",
                "patch_review_item_ids": ["rev-1"],
                "gate_result": {
                    "passed": True,
                    "prompt_validation_passed": True,
                    "transcript_replay_passed": True,
                    "prospect_simulations_passed": True,
                    "eval_cases_passed": True,
                    "eval_cases_present": True
                },
                "created_from": "prompt_patch_preview",
                "runtime_changed": False,
                "active_runtime": False
            }
        }
    }
    await repo.save_prompt_version(**pv_payload)

    # Save control prompt version record
    ctrl_payload = {
        "id": "ctrl-pv-123",
        "file_path": "prompts/final_expense_alex.md",
        "sha": "src_hash_val",
        "created_by": "test-user",
        "change_reason": "Control version",
        "canary_status": "active",
        "qa_thresholds": {
            "prompt_name": "final_expense_alex",
            "version": "0.9",
            "content": "Control content",
        }
    }
    await repo.save_prompt_version(**ctrl_payload)

    exp_payload = {
        "id": "exp-123",
        "experiment_name": "Canary test exp",
        "prompt_version_id": "cand-pv-123",
        "traffic_percent": 1.0,
        "status": "running",
        "metrics": {
            "prompt_name": "final_expense_alex",
            "control_prompt_version_id": "ctrl-pv-123",
            "created_by": "Jimmy",
            "audit_history": [
                {
                    "operation": "create_plan",
                    "actor": "Jimmy",
                    "reason": "Initial setup",
                    "previous_status": None,
                    "new_status": "planned",
                    "timestamp": "2026-05-31T00:00:00Z"
                }
            ],
            "rollback_plan": {
                "action": "rollback",
                "target_status": "rolled_back",
                "traffic_allocation": {
                    "candidate_percent": 0.0,
                    "control_percent": 100.0,
                    "control_prompt_version_id": "ctrl-pv-123"
                }
            }
        }
    }
    await repo.save_deployment_experiment(**exp_payload)
    return "exp-123"


# 1. basic counts test
@pytest.mark.asyncio
async def test_compute_variant_metrics_basic_counts(monitor: CanaryMonitor) -> None:
    data = {
        "calls": [
            {"call_id": "call-1", "outcome": "transfer", "qa_score": 0.95},
            {"call_id": "call-2", "outcome": "callback", "qa_score": 0.90},
            {"call_id": "call-3", "outcome": "hangup", "qa_score": 0.85},
        ],
        "call_turns": [
            {"call_id": "call-1", "turn_number": 1, "speaker": "agent"},
            {"call_id": "call-1", "turn_number": 2, "speaker": "user"},
        ],
        "qa_reports": [],
        "tool_events": [],
        "call_outcome_labels": []
    }
    metrics = monitor.compute_variant_metrics("candidate", data)
    assert metrics.total_calls == 3
    assert metrics.total_turns == 2
    assert metrics.transfers == 1
    assert metrics.callbacks == 1
    assert metrics.hangups == 1
    assert metrics.average_qa_score == 0.90


# 2. transfer_before_consent critical failure
@pytest.mark.asyncio
async def test_detects_candidate_critical_failure(monitor: CanaryMonitor) -> None:
    config = CanaryMonitorConfig(experiment_id="exp-123")
    control = monitor.compute_variant_metrics("control", {})
    candidate_data = {
        "calls": [
            {"call_id": "call-1", "outcome": "transfer", "compliance_flags": {"issues": ["transfer_before_consent"]}}
        ]
    }
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)
    assert any(s.signal_type == "transfer_before_consent_limit_exceeded" and s.rollback_required is True for s in signals)


# 3. DNC failure rollback signal
@pytest.mark.asyncio
async def test_detects_dnc_failure_rollback_signal(monitor: CanaryMonitor) -> None:
    config = CanaryMonitorConfig(experiment_id="exp-123")
    control = monitor.compute_variant_metrics("control", {})
    candidate_data = {
        "calls": [
            {"call_id": "call-1", "outcome": "dnc", "compliance_flags": {"issues": ["continued_talking_after_dnc"]}}
        ]
    }
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)
    assert any(s.signal_type == "dnc_failure_limit_exceeded" and s.rollback_required is True for s in signals)


# 4. wrong_number failure rollback signal
@pytest.mark.asyncio
async def test_detects_wrong_number_failure_rollback_signal(monitor: CanaryMonitor) -> None:
    config = CanaryMonitorConfig(experiment_id="exp-123")
    control = monitor.compute_variant_metrics("control", {})
    candidate_data = {
        "calls": [
            {"call_id": "call-1", "outcome": "wrong_number", "compliance_flags": {"issues": ["continued_talking_after_wrong_number"]}}
        ]
    }
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)
    assert any(s.signal_type == "wrong_number_failure_limit_exceeded" and s.rollback_required is True for s in signals)


# 5. price_quote failure rollback signal
@pytest.mark.asyncio
async def test_detects_price_quote_rollback_signal(monitor: CanaryMonitor) -> None:
    config = CanaryMonitorConfig(experiment_id="exp-123")
    control = monitor.compute_variant_metrics("control", {})
    candidate_data = {
        "calls": [
            {"call_id": "call-1", "outcome": "transfer", "compliance_flags": {"issues": ["agent_price_quote"]}}
        ]
    }
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)
    assert any(s.signal_type == "price_quote_limit_exceeded" and s.rollback_required is True for s in signals)


# 6. licensed_claim failure rollback signal
@pytest.mark.asyncio
async def test_detects_licensed_claim_rollback_signal(monitor: CanaryMonitor) -> None:
    config = CanaryMonitorConfig(experiment_id="exp-123")
    control = monitor.compute_variant_metrics("control", {})
    candidate_data = {
        "calls": [
            {"call_id": "call-1", "outcome": "transfer", "compliance_flags": {"issues": ["agent_claims_licensed"]}}
        ]
    }
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)
    assert any(s.signal_type == "licensed_claim_limit_exceeded" and s.rollback_required is True for s in signals)


# 7. human_claim failure rollback signal
@pytest.mark.asyncio
async def test_detects_human_claim_rollback_signal(monitor: CanaryMonitor) -> None:
    config = CanaryMonitorConfig(experiment_id="exp-123")
    control = monitor.compute_variant_metrics("control", {})
    candidate_data = {
        "calls": [
            {"call_id": "call-1", "outcome": "transfer", "compliance_flags": {"issues": ["agent_claims_human"]}}
        ]
    }
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)
    assert any(s.signal_type == "human_claim_limit_exceeded" and s.rollback_required is True for s in signals)


# 8. you_qualify failure rollback signal
@pytest.mark.asyncio
async def test_detects_you_qualify_rollback_signal(monitor: CanaryMonitor) -> None:
    config = CanaryMonitorConfig(experiment_id="exp-123")
    control = monitor.compute_variant_metrics("control", {})
    candidate_data = {
        "calls": [
            {"call_id": "call-1", "outcome": "transfer", "compliance_flags": {"issues": ["agent_says_you_qualify"]}}
        ]
    }
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)
    assert any(s.signal_type == "you_qualify_failure" and s.rollback_required is True for s in signals)


# 9. QA score drop regression
@pytest.mark.asyncio
async def test_detects_qa_score_drop(monitor: CanaryMonitor) -> None:
    config = CanaryMonitorConfig(experiment_id="exp-123", max_qa_score_drop=0.03)
    control_data = {"calls": [{"call_id": "c-1", "qa_score": 0.95}]}
    candidate_data = {"calls": [{"call_id": "c-2", "qa_score": 0.90}]}
    control = monitor.compute_variant_metrics("control", control_data)
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)
    assert any(s.signal_type == "qa_score_drop_regression" and s.severity == "high" for s in signals)


# 10. Transfer rate drop regression
@pytest.mark.asyncio
async def test_detects_transfer_rate_drop(monitor: CanaryMonitor) -> None:
    config = CanaryMonitorConfig(experiment_id="exp-123", max_transfer_rate_drop=0.10)
    control_data = {"calls": [
        {"call_id": "c-1", "outcome": "transfer"},
        {"call_id": "c-2", "outcome": "transfer"},
    ]}
    candidate_data = {"calls": [
        {"call_id": "c-3", "outcome": "hangup"},
        {"call_id": "c-4", "outcome": "hangup"},
    ]}
    control = monitor.compute_variant_metrics("control", control_data)
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)
    assert any(s.signal_type == "transfer_rate_drop_regression" and s.severity == "high" for s in signals)


# 11. Hangup rate increase regression
@pytest.mark.asyncio
async def test_detects_hangup_rate_increase(monitor: CanaryMonitor) -> None:
    config = CanaryMonitorConfig(experiment_id="exp-123", max_hangup_rate_increase=0.10)
    control_data = {"calls": [
        {"call_id": "c-1", "outcome": "transfer"},
        {"call_id": "c-2", "outcome": "transfer"},
    ]}
    candidate_data = {"calls": [
        {"call_id": "c-3", "outcome": "hangup"},
        {"call_id": "c-4", "outcome": "hangup"},
    ]}
    control = monitor.compute_variant_metrics("control", control_data)
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)
    assert any(s.signal_type == "hangup_rate_increase_regression" and s.severity == "high" for s in signals)


# 12. Insufficient data blocks promotion but not rollback
@pytest.mark.asyncio
async def test_insufficient_data_blocks_promotion_but_not_rollback(repo: Repository, monitor: CanaryMonitor) -> None:
    exp_id = await setup_running_experiment(repo)
    experiment = await monitor.load_experiment(exp_id)
    config = CanaryMonitorConfig(experiment_id=exp_id, min_candidate_calls=25)
    candidate_data = {"calls": [{"call_id": "c-1"}]}
    control_data = {"calls": []}
    control = monitor.compute_variant_metrics("control", control_data)
    candidate = monitor.compute_variant_metrics("candidate", candidate_data)
    signals = monitor.detect_safety_signals(control, candidate, config)

    assert any(s.signal_type == "insufficient_candidate_calls" and s.rollback_required is False for s in signals)

    readiness = await monitor.check_promotion_readiness(
        experiment, control, candidate, signals, config
    )
    assert readiness.ready is False
    assert any("calls" in b and "below minimum" in b for b in readiness.blockers)


# 13. Monitor updates experiment metrics
@pytest.mark.asyncio
async def test_monitor_updates_experiment_metrics(repo: Repository, monitor: CanaryMonitor, tmp_path: Path) -> None:
    exp_id = await setup_running_experiment(repo)
    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=0,
        min_control_calls=0,
        output_dir=tmp_path
    )
    result = await monitor.monitor_experiment(config)
    assert result.metrics_updated is True

    exp = await repo.get_deployment_experiment(exp_id)
    assert "monitoring_history" in exp["metrics"]
    assert "latest_monitoring_result" in exp["metrics"]
    assert len(exp["metrics"]["monitoring_history"]) > 0


# 14. Auto rollback disabled does not rollback
@pytest.mark.asyncio
async def test_auto_rollback_disabled_does_not_rollback(repo: Repository, monitor: CanaryMonitor, tmp_path: Path) -> None:
    exp_id = await setup_running_experiment(repo)
    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=0,
        min_control_calls=0,
        auto_rollback=False,
        output_dir=tmp_path
    )

    await repo.save_qa_report(call_id="call-cand", scores={}, issues=["transfer_before_consent"])
    await repo.save_call(call_id="call-cand", compliance_flags={"prompt_version_id": "cand-pv-123"}, outcome="transfer")

    result = await monitor.monitor_experiment(config)
    assert result.rollback_triggered is False
    assert result.status_after == "running"


# 15. Auto rollback enabled rolls back
@pytest.mark.asyncio
async def test_auto_rollback_enabled_rolls_back(repo: Repository, monitor: CanaryMonitor, tmp_path: Path) -> None:
    exp_id = await setup_running_experiment(repo)
    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=0,
        min_control_calls=0,
        auto_rollback=True,
        output_dir=tmp_path
    )

    await repo.save_qa_report(call_id="call-cand", scores={}, issues=["transfer_before_consent"])
    await repo.save_call(call_id="call-cand", compliance_flags={"prompt_version_id": "cand-pv-123"}, outcome="transfer")

    result = await monitor.monitor_experiment(config)
    assert result.rollback_triggered is True
    assert result.status_after == "rolled_back"

    exp = await repo.get_deployment_experiment(exp_id)
    assert exp["status"] == "rolled_back"
    assert len(exp["metrics"]["audit_history"]) > 1


# 16. Promotion readiness true for clean metrics
@pytest.mark.asyncio
async def test_promotion_readiness_true_for_clean_metrics(repo: Repository, monitor: CanaryMonitor) -> None:
    exp_id = await setup_running_experiment(repo)
    experiment = await monitor.load_experiment(exp_id)

    control_calls = [{"call_id": f"ctrl-{i}", "qa_score": 0.95} for i in range(25)]
    candidate_calls = [{"call_id": f"cand-{i}", "qa_score": 0.94} for i in range(25)]

    for c in control_calls:
        await repo.save_call(compliance_flags={"prompt_version_id": "ctrl-pv-123"}, **c)
    for c in candidate_calls:
        await repo.save_call(compliance_flags={"prompt_version_id": "cand-pv-123"}, **c)

    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=25,
        min_control_calls=25
    )

    data_bundle = await monitor.gather_canary_data(experiment, config)
    split_data = monitor.split_by_variant(experiment, data_bundle)
    control = monitor.compute_variant_metrics("control", split_data["control"])
    candidate = monitor.compute_variant_metrics("candidate", split_data["candidate"])
    signals = monitor.detect_safety_signals(control, candidate, config)

    readiness = await monitor.check_promotion_readiness(
        experiment, control, candidate, signals, config
    )
    assert readiness.ready is True
    assert "human approval" in readiness.recommended_next_step.lower()


# 17. Promotion readiness false with critical signal
@pytest.mark.asyncio
async def test_promotion_readiness_false_with_critical_signal(repo: Repository, monitor: CanaryMonitor) -> None:
    exp_id = await setup_running_experiment(repo)
    experiment = await monitor.load_experiment(exp_id)

    control = monitor.compute_variant_metrics("control", {})
    candidate = CanaryVariantMetrics(variant="candidate", total_calls=25, transfer_before_consent_count=1)

    config = CanaryMonitorConfig(experiment_id=exp_id, min_candidate_calls=25, min_control_calls=0)
    signals = monitor.detect_safety_signals(control, candidate, config)

    readiness = await monitor.check_promotion_readiness(
        experiment, control, candidate, signals, config
    )
    assert readiness.ready is False
    assert len(readiness.blockers) > 0


# 18. Promotion readiness false with missing gate
@pytest.mark.asyncio
async def test_promotion_readiness_false_with_missing_gate(repo: Repository, monitor: CanaryMonitor) -> None:
    # Save a candidate prompt version record with missing gate_result
    pv_payload = {
        "id": "cand-pv-no-gate",
        "file_path": "prompts/final_expense_alex.md",
        "sha": "cand_hash_val",
        "created_by": "test-user",
        "change_reason": "Testing",
        "canary_status": "candidate",
        "qa_thresholds": {
            "prompt_name": "final_expense_alex",
            "version": "1.0",
            "content": "Candidate content",
            "metadata": {
                # missing gate_result
            }
        }
    }
    await repo.save_prompt_version(**pv_payload)

    exp_payload = {
        "id": "exp-no-gate",
        "experiment_name": "No gate exp",
        "prompt_version_id": "cand-pv-no-gate",
        "traffic_percent": 1.0,
        "status": "running",
        "metrics": {"control_prompt_version_id": "ctrl-pv-123"}
    }
    await repo.save_deployment_experiment(**exp_payload)
    experiment = await monitor.load_experiment("exp-no-gate")

    control = CanaryVariantMetrics(variant="control", total_calls=25)
    candidate = CanaryVariantMetrics(variant="candidate", total_calls=25)
    config = CanaryMonitorConfig(experiment_id="exp-no-gate", min_candidate_calls=25, min_control_calls=25)
    signals = monitor.detect_safety_signals(control, candidate, config)

    readiness = await monitor.check_promotion_readiness(
        experiment, control, candidate, signals, config
    )
    assert readiness.ready is False
    assert any("gate result" in b.lower() for b in readiness.blockers)


# 19. split_by_variant uses prompt_version_id
@pytest.mark.asyncio
async def test_split_by_variant_uses_prompt_version_id(monitor: CanaryMonitor) -> None:
    experiment = {
        "prompt_version_id": "candidate-pv",
        "metrics": {"control_prompt_version_id": "control-pv"}
    }
    data_bundle = {
        "calls": [
            {"call_id": "call-1", "prompt_version_id": "candidate-pv"},
            {"call_id": "call-2", "prompt_version_id": "control-pv"},
        ]
    }
    split = monitor.split_by_variant(experiment, data_bundle)
    assert len(split["candidate"]["calls"]) == 1
    assert split["candidate"]["calls"][0]["call_id"] == "call-1"
    assert len(split["control"]["calls"]) == 1
    assert split["control"]["calls"][0]["call_id"] == "call-2"


# 20. split_by_variant unknown when missing attribution
@pytest.mark.asyncio
async def test_split_by_variant_unknown_when_missing_attribution(monitor: CanaryMonitor) -> None:
    experiment = {
        "prompt_version_id": "candidate-pv",
        "metrics": {"control_prompt_version_id": "control-pv"}
    }
    data_bundle = {
        "calls": [
            {"call_id": "call-1"},  # missing attribution
        ]
    }
    split = monitor.split_by_variant(experiment, data_bundle)
    assert len(split["unknown"]["calls"]) == 1


# 21. read_json_reports ignores malformed JSON
def test_read_json_reports_ignores_malformed_json(monitor: CanaryMonitor, tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("invalid json contents", encoding="utf-8")

    good_file = tmp_path / "good.json"
    good_file.write_text('{"name": "good report", "passed": true}', encoding="utf-8")

    reports = monitor.read_json_reports([tmp_path])
    assert len(reports) == 1
    assert reports[0]["name"] == "good report"


# 22. monitoring report files written
@pytest.mark.asyncio
async def test_monitoring_report_files_written(repo: Repository, monitor: CanaryMonitor, tmp_path: Path) -> None:
    exp_id = await setup_running_experiment(repo)
    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=0,
        min_control_calls=0,
        output_dir=tmp_path
    )
    result = await monitor.monitor_experiment(config)
    assert result.report_json_path is not None
    assert result.report_markdown_path is not None
    assert Path(result.report_json_path).exists()
    assert Path(result.report_markdown_path).exists()


# 23. CLI monitor outputs JSON
@pytest.mark.asyncio
async def test_cli_monitor_outputs_json(repo: Repository, tmp_path: Path) -> None:
    exp_id = await setup_running_experiment(repo)
    env = dict(os.environ)
    env["DANA_DATA_DIR"] = str(repo.store._data_dir)
    env["PYTHONPATH"] = str(Path.cwd())

    import subprocess
    cmd = [
        sys.executable,
        "scripts/monitor_canary_rollout.py",
        "monitor",
        "--experiment-id", exp_id,
        "--min-candidate-calls", "0",
        "--min-control-calls", "0",
        "--output-dir", str(tmp_path)
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0
    stdout_json = json.loads(proc.stdout)
    assert stdout_json["experiment_id"] == exp_id


# 24. CLI rollback check exits 1 when rollback required
@pytest.mark.asyncio
async def test_cli_rollback_check_exits_1_when_rollback_required(repo: Repository) -> None:
    exp_id = await setup_running_experiment(repo)
    await repo.save_qa_report(call_id="call-cand", scores={}, issues=["transfer_before_consent"])
    await repo.save_call(call_id="call-cand", compliance_flags={"prompt_version_id": "cand-pv-123"}, outcome="transfer")

    env = dict(os.environ)
    env["DANA_DATA_DIR"] = str(repo.store._data_dir)
    env["PYTHONPATH"] = str(Path.cwd())

    import subprocess
    cmd = [
        sys.executable,
        "scripts/monitor_canary_rollout.py",
        "rollback-check",
        "--experiment-id", exp_id
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    res = json.loads(proc.stderr)
    assert res["rollback_required"] is True
    assert len(res["signals"]) > 0


# 25. CLI readiness exits 1 when not ready
@pytest.mark.asyncio
async def test_cli_readiness_exits_1_when_not_ready(repo: Repository) -> None:
    exp_id = await setup_running_experiment(repo)
    env = dict(os.environ)
    env["DANA_DATA_DIR"] = str(repo.store._data_dir)
    env["PYTHONPATH"] = str(Path.cwd())

    import subprocess
    cmd = [
        sys.executable,
        "scripts/monitor_canary_rollout.py",
        "readiness",
        "--experiment-id", exp_id,
        "--min-candidate-calls", "25",
        "--min-control-calls", "25"
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    res = json.loads(proc.stderr)
    assert res["ready"] is False


# 26. CLI readiness exits 0 when ready
@pytest.mark.asyncio
async def test_cli_readiness_exits_0_when_ready(repo: Repository) -> None:
    exp_id = await setup_running_experiment(repo)
    for i in range(25):
        await repo.save_call(compliance_flags={"prompt_version_id": "ctrl-pv-123"}, call_id=f"ctrl-{i}", qa_score=0.95)
        await repo.save_call(compliance_flags={"prompt_version_id": "cand-pv-123"}, call_id=f"cand-{i}", qa_score=0.95)

    env = dict(os.environ)
    env["DANA_DATA_DIR"] = str(repo.store._data_dir)
    env["PYTHONPATH"] = str(Path.cwd())

    import subprocess
    cmd = [
        sys.executable,
        "scripts/monitor_canary_rollout.py",
        "readiness",
        "--experiment-id", exp_id,
        "--min-candidate-calls", "25",
        "--min-control-calls", "25"
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert proc.returncode == 0
    res = json.loads(proc.stdout)
    assert res["ready"] is True


# 27. no live prompt file modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(repo: Repository, monitor: CanaryMonitor, tmp_path: Path) -> None:
    live_prompt_path = Path("prompts/final_expense_alex.md")
    original_content = ""
    if live_prompt_path.exists():
        original_content = live_prompt_path.read_text(encoding="utf-8")

    exp_id = await setup_running_experiment(repo)
    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=0,
        min_control_calls=0,
        output_dir=tmp_path
    )

    await monitor.monitor_experiment(config)

    if live_prompt_path.exists():
        assert live_prompt_path.read_text(encoding="utf-8") == original_content


# 28. monitor does not activate prompt version
@pytest.mark.asyncio
async def test_monitor_does_not_activate_prompt_version(repo: Repository, monitor: CanaryMonitor, tmp_path: Path) -> None:
    exp_id = await setup_running_experiment(repo)
    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=0,
        min_control_calls=0,
        output_dir=tmp_path
    )

    await monitor.monitor_experiment(config)

    pv = await repo.get_prompt_version("cand-pv-123")
    assert pv["canary_status"] == "candidate"


# 29. monitor does not change runtime default behavior
@pytest.mark.asyncio
async def test_monitor_does_not_change_runtime_default_behavior(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DANA_ENABLE_PROMPT_CANARY", raising=False)
    resolver = PromptResolver(repository=repo)
    pv_id = await resolver.resolve_prompt_version_id("final_expense_alex", "call-test")
    assert pv_id is None


# 30. metrics history preserves existing audit history
@pytest.mark.asyncio
async def test_metrics_history_preserves_existing_audit_history(repo: Repository, monitor: CanaryMonitor, tmp_path: Path) -> None:
    exp_id = await setup_running_experiment(repo)
    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=0,
        min_control_calls=0,
        output_dir=tmp_path
    )

    await monitor.monitor_experiment(config)

    exp = await repo.get_deployment_experiment(exp_id)
    assert len(exp["metrics"]["audit_history"]) == 1
    assert exp["metrics"]["audit_history"][0]["operation"] == "create_plan"


# 31. monitor handles no data gracefully
@pytest.mark.asyncio
async def test_monitor_handles_no_data_gracefully(repo: Repository, monitor: CanaryMonitor, tmp_path: Path) -> None:
    exp_id = await setup_running_experiment(repo)
    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=25,
        min_control_calls=25,
        output_dir=tmp_path
    )

    result = await monitor.monitor_experiment(config)
    assert result.experiment_id == exp_id
    assert len(result.safety_signals) > 0
    assert result.promotion_ready is False


# 32. test_gather_canary_data_includes_canary_variant_only_calls
@pytest.mark.asyncio
async def test_gather_canary_data_includes_canary_variant_only_calls(repo: Repository, monitor: CanaryMonitor) -> None:
    exp_id = await setup_running_experiment(repo)
    experiment = await monitor.load_experiment(exp_id)

    # Save calls with canary_variant="candidate" and canary_variant="control"
    # but no prompt_version_id and no experiment_id.
    # To survive Call schema stripping, we store them in compliance_flags.
    await repo.save_call(call_id="var-cand-1", compliance_flags={"canary_variant": "candidate"})
    await repo.save_call(call_id="var-ctrl-1", compliance_flags={"canary_variant": "control"})

    # Also save one nested under metadata to test the nested check
    await repo.save_call(call_id="meta-cand-1", compliance_flags={"metadata": {"canary_variant": "candidate"}})

    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=1,
        min_control_calls=1
    )

    data_bundle = await monitor.gather_canary_data(experiment, config)
    assert any(c["call_id"] == "var-cand-1" for c in data_bundle["calls"])
    assert any(c["call_id"] == "var-ctrl-1" for c in data_bundle["calls"])
    assert any(c["call_id"] == "meta-cand-1" for c in data_bundle["calls"])

    split_data = monitor.split_by_variant(experiment, data_bundle)
    assert any(c["call_id"] == "var-cand-1" for c in split_data["candidate"]["calls"])
    assert any(c["call_id"] == "var-ctrl-1" for c in split_data["control"]["calls"])
    assert any(c["call_id"] == "meta-cand-1" for c in split_data["candidate"]["calls"])


# 33. test_gather_canary_data_includes_use_candidate_only_calls
@pytest.mark.asyncio
async def test_gather_canary_data_includes_use_candidate_only_calls(repo: Repository, monitor: CanaryMonitor) -> None:
    exp_id = await setup_running_experiment(repo)
    experiment = await monitor.load_experiment(exp_id)

    # Save calls with use_candidate=True and use_candidate=False
    # but no prompt_version_id and no experiment_id
    await repo.save_call(call_id="use-cand-1", compliance_flags={"use_candidate": True})
    await repo.save_call(call_id="use-ctrl-1", compliance_flags={"use_candidate": False})

    # Also save one nested under metadata
    await repo.save_call(call_id="meta-use-cand-1", compliance_flags={"metadata": {"use_candidate": True}})

    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=1,
        min_control_calls=1
    )

    data_bundle = await monitor.gather_canary_data(experiment, config)
    assert any(c["call_id"] == "use-cand-1" for c in data_bundle["calls"])
    assert any(c["call_id"] == "use-ctrl-1" for c in data_bundle["calls"])
    assert any(c["call_id"] == "meta-use-cand-1" for c in data_bundle["calls"])

    split_data = monitor.split_by_variant(experiment, data_bundle)
    assert any(c["call_id"] == "use-cand-1" for c in split_data["candidate"]["calls"])
    assert any(c["call_id"] == "use-ctrl-1" for c in split_data["control"]["calls"])
    assert any(c["call_id"] == "meta-use-cand-1" for c in split_data["candidate"]["calls"])


# 34. test_gather_canary_data_warns_on_variant_only_missing_experiment_id
@pytest.mark.asyncio
async def test_gather_canary_data_warns_on_variant_only_missing_experiment_id(repo: Repository, monitor: CanaryMonitor) -> None:
    exp_id = await setup_running_experiment(repo)
    experiment = await monitor.load_experiment(exp_id)

    await repo.save_call(call_id="warn-cand-1", compliance_flags={"canary_variant": "candidate"})

    config = CanaryMonitorConfig(
        experiment_id=exp_id,
        min_candidate_calls=1,
        min_control_calls=1
    )

    data_bundle = await monitor.gather_canary_data(experiment, config)
    assert "Included call with variant attribution but missing experiment_id; verify routing metadata." in data_bundle["warnings"]
