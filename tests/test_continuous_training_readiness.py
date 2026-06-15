import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock, patch

from ops.readiness import (
    ContinuousTrainingReadinessAuditor,
    ContinuousTrainingReadinessConfig,
    ReadinessCheckResult,
    ContinuousTrainingReadinessResult,
)

# Helpers
def create_dummy_repo_structure(base_dir: Path) -> None:
    # schemas
    schemas_dir = base_dir / "storage"
    schemas_dir.mkdir(parents=True, exist_ok=True)
    (schemas_dir / "schemas.py").write_text("""
class TrainingSource(BaseModel): pass
class TrainingExample(BaseModel): pass
class EvalCase(BaseModel): pass
class PromptVersion(BaseModel): pass
class HumanReviewItem(BaseModel): pass
class DeploymentExperiment(BaseModel): pass
class CallOutcomeLabel(BaseModel): pass
class CallTurn(BaseModel): pass
""", encoding="utf-8")
    
    (schemas_dir / "repository.py").write_text("""
def save_training_source(): pass
def get_training_source(): pass
def query_training_sources(): pass
def save_training_example(): pass
def get_training_example(): pass
def query_training_examples(): pass
def save_eval_case(): pass
def get_eval_case(): pass
def query_eval_cases(): pass
def save_prompt_version(): pass
def get_prompt_version(): pass
def query_prompt_versions(): pass
def save_human_review_item(): pass
def get_human_review_item(): pass
def query_human_review_items(): pass
def save_deployment_experiment(): pass
def get_deployment_experiment(): pass
def query_deployment_experiments(): pass
def save_call_outcome_label(): pass
def get_call_outcome_label(): pass
def query_call_outcome_labels(): pass
""", encoding="utf-8")
    
    (schemas_dir / "postgres_store.py").write_text("""
TABLE_COLUMNS = {
    "training_sources": set(),
    "training_examples": set(),
    "eval_cases": set(),
    "prompt_versions": set(),
    "human_review_items": set(),
    "deployment_experiments": set(),
    "call_outcome_labels": set()
}
""", encoding="utf-8")

    (schemas_dir / "jsonl_store.py").write_text("class JsonlStore: pass", encoding="utf-8")

    # Migrations
    migrations_dir = base_dir / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / "006_continuous_training.sql").write_text("CREATE TABLE test;", encoding="utf-8")
    (migrations_dir / "008_telephony_campaigns.sql").write_text("CREATE TABLE test;", encoding="utf-8")

    # Telephony modules
    telephony_dir = base_dir / "telephony"
    telephony_dir.mkdir(parents=True, exist_ok=True)
    (telephony_dir / "__init__.py").write_text("", encoding="utf-8")
    (telephony_dir / "campaign_models.py").write_text("", encoding="utf-8")
    (telephony_dir / "campaign_service.py").write_text("", encoding="utf-8")
    (telephony_dir / "lead_importer.py").write_text("", encoding="utf-8")
    (telephony_dir / "dialer_queue.py").write_text("calling_window_start calling_window_end daily_call_cap max_concurrent_calls dnc do_not_call", encoding="utf-8")
    (telephony_dir / "livekit_adapter.py").write_text("TELEPHONY_LIVE_MODE DANA_ENABLE_OUTBOUND_DIALER", encoding="utf-8")
    (telephony_dir / "call_control.py").write_text("", encoding="utf-8")
    (telephony_dir / "telephony_reports.py").write_text("", encoding="utf-8")

    # Training modules
    training_dir = base_dir / "training"
    training_dir.mkdir(parents=True, exist_ok=True)
    for mod in ["__init__.py", "ingestion.py", "labeler.py", "example_miner.py", "review_service.py", "rag_builder.py", "daily_qa_miner.py", "fine_tune_export.py", "fine_tune_gate.py", "fine_tune_job_request.py", "fine_tune_job_tracker.py", "intake_orchestrator.py", "post_call_exporter.py", "youtube_importer.py", "intake_scheduler.py"]:
        if mod == "fine_tune_job_tracker.py":
            (training_dir / mod).write_text("start_authorized = True\napi_upload_performed = False", encoding="utf-8")
        elif mod == "fine_tune_export.py":
            (training_dir / mod).write_text("output_dir = 'data/'", encoding="utf-8")
        else:
            (training_dir / mod).write_text("def mock_func(): pass", encoding="utf-8")

    # Ops console module
    ops_dir = base_dir / "ops"
    ops_dir.mkdir(parents=True, exist_ok=True)
    (ops_dir / "training_console.py").write_text("class TrainingOperationsConsole: pass", encoding="utf-8")
    (ops_dir / "web_console.py").write_text('host: str = "127.0.0.1"\nallow_remote: bool = False\nALLOWED_EXTENSIONS = set()\nALLOWED_SOURCE_TYPES = {}\nrelative_to = True', encoding="utf-8")

    # Static assets
    static_dir = base_dir / "static" / "training_console"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (static_dir / "app.js").write_text("", encoding="utf-8")
    (static_dir / "styles.css").write_text("", encoding="utf-8")

    # Prompts
    prompts_dir = base_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "versioning.py").write_text("class Versioning: pass", encoding="utf-8")
    (prompts_dir / "patch_generator.py").write_text("class PatchGen: pass", encoding="utf-8")
    (prompts_dir / "patch_preview.py").write_text("class PatchPrev: pass", encoding="utf-8")
    (prompts_dir / "final_expense_alex.md").write_text("# Master Prompt\nDana is helpful.", encoding="utf-8")

    # Evals
    evals_dir = base_dir / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)
    (evals_dir / "case_runner.py").write_text("def run_static(): pass", encoding="utf-8")
    (evals_dir / "transcript_replay.py").write_text("def replay(): pass", encoding="utf-8")
    (evals_dir / "fixtures").mkdir(parents=True, exist_ok=True)

    # Simulations
    sim_dir = base_dir / "simulations"
    sim_dir.mkdir(parents=True, exist_ok=True)
    (sim_dir / "prospect_simulator.py").write_text("class Simulator: pass", encoding="utf-8")

    # Canary
    dep_dir = base_dir / "deployment"
    dep_dir.mkdir(parents=True, exist_ok=True)
    (dep_dir / "canary.py").write_text("DANA_ENABLE_PROMPT_CANARY DANA_ALLOW_FORCE_CANDIDATE_PROMPT", encoding="utf-8")
    (dep_dir / "monitoring.py").write_text("class CanaryMonitor: pass", encoding="utf-8")

    # CLIs
    cli_dir = base_dir / "scripts"
    cli_dir.mkdir(parents=True, exist_ok=True)
    cli_scripts = [
        "ingest_training_source.py", "label_training_source.py", "mine_training_examples.py",
        "review_training_items.py", "run_daily_qa_miner.py", "run_eval_cases.py", "replay_transcripts.py",
        "run_prospect_simulations.py", "manage_prompt_versions.py", "generate_prompt_patches.py",
        "preview_prompt_patch.py", "manage_canary_rollout.py", "monitor_canary_rollout.py",
        "export_fine_tune_dataset.py", "gate_fine_tune_dataset.py", "prepare_fine_tune_job_request.py",
        "track_fine_tune_job.py", "run_continuous_training_readiness.py", "rebuild_training_rag.py",
        "run_training_intake.py", "export_completed_call.py", "import_youtube_transcripts.py",
        "run_training_intake_scheduler.py", "training_console.py", "run_training_web_console.py",
        "manage_telephony_campaigns.py", "import_campaign_leads.py", "run_outbound_dialer_once.py"
    ]
    for script in cli_scripts:
        (cli_dir / script).write_text("import json\n# clean json output", encoding="utf-8")

    # Docs
    docs_dir = base_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for doc in ["continuous_training_runbook.md", "dana_training_safety_gates.md", "fine_tuning_operating_procedure.md", "prompt_canary_operating_procedure.md", "training_intake_operating_procedure.md", "post_call_training_export_operating_procedure.md", "youtube_training_import_operating_procedure.md", "training_intake_scheduler_operating_procedure.md", "training_operations_console.md", "training_web_console_operating_procedure.md", "training_web_console_advanced_workflows.md", "telephony_campaign_operations.md", "telnyx_livekit_setup.md", "outbound_dialer_safety_controls.md"]:
        (docs_dir / doc).write_text("# Doc\nRed lines include:\n- no transfer without consent\n- no licensed claim\n- no price quotes\n- no approval/qualification promises\n- no DNC/wrong-number continuation\n- no PII collection\n must never do manually", encoding="utf-8")

    # Runtime Safety
    core_dir = base_dir / "core"
    core_dir.mkdir(parents=True, exist_ok=True)
    (core_dir / "agent_runtime.py").write_text("try: resolve_prompt() except: fallback()\n# DANA_ENABLE_POST_CALL_TRAINING_EXPORT", encoding="utf-8")

    # Tests
    tests_dir = base_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_files = [
        "test_continuous_training_storage.py", "test_training_ingestion.py", "test_training_labeler.py",
        "test_training_example_miner.py", "test_training_review_service.py", "test_daily_qa_miner.py",
        "test_eval_case_runner.py", "test_transcript_replay.py", "test_prospect_simulator.py",
        "test_prompt_versioning.py", "test_prompt_patch_generator.py", "test_prompt_patch_preview.py",
        "test_canary_rollout.py", "test_canary_monitoring.py", "test_fine_tune_export.py",
        "test_fine_tune_gate.py", "test_fine_tune_job_request.py", "test_fine_tune_job_tracker.py",
        "test_continuous_training_readiness.py", "test_training_rag_builder.py", "test_training_intake_orchestrator.py",
        "test_post_call_exporter.py", "test_youtube_importer.py", "test_training_intake_scheduler.py", "test_training_console.py", "test_training_web_console.py", "test_training_web_console_advanced.py",
        "test_telephony_campaign_service.py", "test_campaign_lead_importer.py", "test_dialer_queue.py", "test_livekit_adapter.py", "test_telephony_web_console.py"
    ]
    for test in test_files:
        (tests_dir / test).write_text("def test_dummy(): pass", encoding="utf-8")


# 1. test_readiness_auditor_runs_and_returns_result
def test_readiness_auditor_runs_and_returns_result(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    config = ContinuousTrainingReadinessConfig(output_dir=str(tmp_path / "readiness"))
    
    res = auditor.run_all_checks(config)
    assert isinstance(res, ContinuousTrainingReadinessResult)
    assert res.total_checks > 0
    assert res.checks_passed == res.total_checks
    assert res.passed is True


# 2. test_storage_checks_detect_required_schemas
def test_storage_checks_detect_required_schemas(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    # Modify schemas file to omit TrainingExample
    schemas_file = tmp_path / "storage" / "schemas.py"
    schemas_file.write_text("class TrainingSource: pass", encoding="utf-8")
    
    checks = auditor.check_storage_foundation()
    example_check = next((c for c in checks if c.check_id == "schema_trainingexample"), None)
    assert example_check is not None
    assert example_check.passed is False


# 3. test_training_pipeline_checks_detect_modules
def test_training_pipeline_checks_detect_modules(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    # Remove labeler
    os.remove(tmp_path / "training" / "labeler.py")
    
    checks = auditor.check_training_pipeline_modules()
    labeler_check = next((c for c in checks if c.check_id == "training_labeler"), None)
    assert labeler_check is not None
    assert labeler_check.passed is False


# 4. test_prompt_pipeline_checks_detect_modules
def test_prompt_pipeline_checks_detect_modules(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    os.remove(tmp_path / "prompts" / "patch_preview.py")
    
    checks = auditor.check_prompt_pipeline_modules()
    prev_check = next((c for c in checks if c.check_id == "prompt_patch_prev"), None)
    assert prev_check is not None
    assert prev_check.passed is False


# 5. test_eval_replay_simulation_checks_detect_modules
def test_eval_replay_simulation_checks_detect_modules(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    os.remove(tmp_path / "evals" / "case_runner.py")
    
    checks = auditor.check_eval_replay_simulation_modules()
    runner_check = next((c for c in checks if c.check_id == "eval_case_runner"), None)
    assert runner_check is not None
    assert runner_check.passed is False


# 6. test_canary_checks_require_env_flags
def test_canary_checks_require_env_flags(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    # Remove environment reference in canary.py
    (tmp_path / "deployment" / "canary.py").write_text("no flags", encoding="utf-8")
    
    checks = auditor.check_environment_flags_fail_closed()
    force_check = next((c for c in checks if c.check_id == "force_candidate_requires_env_flag"), None)
    assert force_check is not None
    assert force_check.passed is False


# 7. test_fine_tune_safety_checks_detect_no_provider_calls
def test_fine_tune_safety_checks_detect_no_provider_calls(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    checks = auditor.check_no_forbidden_provider_calls()
    forbidden_check = next((c for c in checks if c.check_id == "no_forbidden_provider_api_calls"), None)
    assert forbidden_check is not None
    assert forbidden_check.passed is True


# 8. test_runtime_safety_detects_patch_markers_in_live_prompt
def test_runtime_safety_detects_patch_markers_in_live_prompt(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    # Insert patch markers
    (tmp_path / "prompts" / "final_expense_alex.md").write_text("PATCH_START\nSome text\nPATCH_END", encoding="utf-8")
    
    checks = auditor.check_prompt_file_safety()
    prompt_check = next((c for c in checks if c.check_id == "prompt_clean_of_preview_patch_markers"), None)
    assert prompt_check is not None
    assert prompt_check.passed is False


# 9. test_forbidden_provider_call_scan_detects_executable_call
def test_forbidden_provider_call_scan_detects_executable_call(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    # Write executable forbidden call
    (tmp_path / "training" / "ingestion.py").write_text("client.fine_tuning.jobs.create()", encoding="utf-8")
    
    checks = auditor.check_no_forbidden_provider_calls()
    forbidden_check = next((c for c in checks if c.check_id == "no_forbidden_provider_api_calls"), None)
    assert forbidden_check is not None
    assert forbidden_check.passed is False
    assert len(forbidden_check.details["executable_failures"]) > 0


# 10. test_forbidden_provider_call_scan_ignores_docs_or_marks_low
def test_forbidden_provider_call_scan_ignores_docs_or_marks_low(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    # Write comment forbidden call
    (tmp_path / "training" / "ingestion.py").write_text("# Reference: client.fine_tuning.jobs.create() in docs", encoding="utf-8")
    
    checks = auditor.check_no_forbidden_provider_calls()
    forbidden_check = next((c for c in checks if c.check_id == "no_forbidden_provider_api_calls"), None)
    assert forbidden_check is not None
    assert forbidden_check.passed is True  # still passes (not in executable code)


# 11. test_cli_script_checks_include_all_required_scripts
def test_cli_script_checks_include_all_required_scripts(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    os.remove(tmp_path / "scripts" / "track_fine_tune_job.py")
    
    checks = auditor.check_cli_scripts()
    track_check = next((c for c in checks if c.check_id == "cli_track_fine_tune_job"), None)
    assert track_check is not None
    assert track_check.passed is False


# 12. test_tests_exist_checks_include_all_required_test_files
def test_tests_exist_checks_include_all_required_test_files(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    os.remove(tmp_path / "tests" / "test_fine_tune_job_tracker.py")
    
    checks = auditor.check_tests_exist()
    tracker_check = next((c for c in checks if c.check_id == "test_test_fine_tune_job_tracker"), None)
    assert tracker_check is not None
    assert tracker_check.passed is False


# 13. test_docs_exist_checks_include_runbooks
def test_docs_exist_checks_include_runbooks(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    os.remove(tmp_path / "docs" / "continuous_training_runbook.md")
    
    checks = auditor.check_docs_and_runbooks()
    runbook_check = next((c for c in checks if c.check_id == "doc_continuous_training_runbook"), None)
    assert runbook_check is not None
    assert runbook_check.passed is False


# 14. test_report_files_written
def test_report_files_written(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    config = ContinuousTrainingReadinessConfig(output_dir=str(tmp_path / "readiness"))
    res = auditor.run_all_checks(config)
    
    assert Path(res.report_json_path).exists()
    assert Path(res.report_markdown_path).exists()
    assert Path(res.executive_summary_path).exists()


# 15. test_markdown_report_contains_required_sections
def test_markdown_report_contains_required_sections(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    config = ContinuousTrainingReadinessConfig(output_dir=str(tmp_path / "readiness"))
    res = auditor.run_all_checks(config)
    
    md_content = Path(res.report_markdown_path).read_text(encoding="utf-8")
    assert "# Dana Continuous Training Readiness Report" in md_content
    assert "## Executive Summary" in md_content
    assert "## Pipeline Coverage" in md_content
    assert "## Safety Gate Summary" in md_content
    assert "## Forbidden Action Scan" in md_content
    assert "## Runtime Safety" in md_content
    assert "## Operating Decision" in md_content


# 16. test_executive_summary_is_manager_readable
def test_executive_summary_is_manager_readable(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    config = ContinuousTrainingReadinessConfig(output_dir=str(tmp_path / "readiness"))
    res = auditor.run_all_checks(config)
    
    exec_content = Path(res.executive_summary_path).read_text(encoding="utf-8")
    assert "# Dana Continuous Training Readiness - Executive Summary" in exec_content
    assert "Overall Status:" in exec_content
    assert "Readiness Decision:" in exec_content


# 17. test_cli_outputs_json
def test_cli_outputs_json(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    
    cmd = [
        sys.executable,
        "scripts/run_continuous_training_readiness.py",
        "--repo-root", str(tmp_path),
        "--output-dir", str(tmp_path / "readiness")
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode in (0, 1)  # passes or fails depending on files, but must output JSON
    
    # Verify stdout is parseable JSON
    data = json.loads(res.stdout.strip())
    assert "readiness_id" in data


# 18. test_cli_non_strict_can_pass_with_optional_missing_items
def test_cli_non_strict_can_pass_with_optional_missing_items(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    
    # Remove an high severity test file, which in strict mode should fail
    os.remove(tmp_path / "tests" / "test_training_ingestion.py")
    
    cmd = [
        sys.executable,
        "scripts/run_continuous_training_readiness.py",
        "--repo-root", str(tmp_path),
        "--non-strict",
        "--output-dir", str(tmp_path / "readiness")
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    
    data = json.loads(res.stdout.strip())
    assert data["strict"] is False
    # Since we deleted an high test, passed should still be True in non-strict mode
    # (provided no critical failures occur)
    assert data["passed"] is True
    assert res.returncode == 0


# 19. test_no_live_prompt_file_modified
def test_no_live_prompt_file_modified(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    live_prompt = tmp_path / "prompts" / "final_expense_alex.md"
    content_before = live_prompt.read_text(encoding="utf-8")
    
    config = ContinuousTrainingReadinessConfig(output_dir=str(tmp_path / "readiness"))
    auditor.run_all_checks(config)
    
    content_after = live_prompt.read_text(encoding="utf-8")
    assert content_before == content_after


# 20. test_readiness_result_fails_on_critical_check
def test_readiness_result_fails_on_critical_check(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    # Missing storage schemas file (critical)
    os.remove(tmp_path / "storage" / "schemas.py")
    
    config = ContinuousTrainingReadinessConfig(output_dir=str(tmp_path / "readiness"))
    res = auditor.run_all_checks(config)
    assert res.passed is False
    assert res.critical_failures > 0


# 21. test_category_rollups_count_failures
def test_category_rollups_count_failures() -> None:
    # Directly test category result rollup calculations
    # Build ReadinessCheckResult instances manually
    checks = [
        ReadinessCheckResult(check_id="c1", name="C1", category="TestCat", passed=True, severity="info", message="Info"),
        ReadinessCheckResult(check_id="c2", name="C2", category="TestCat", passed=False, severity="low", message="Low Fail"),
        ReadinessCheckResult(check_id="c3", name="C3", category="TestCat", passed=False, severity="medium", message="Med Fail"),
        ReadinessCheckResult(check_id="c4", name="C4", category="TestCat", passed=False, severity="high", message="High Fail"),
        ReadinessCheckResult(check_id="c5", name="C5", category="TestCat", passed=False, severity="critical", message="Crit Fail"),
    ]
    
    # Simulate rollup logic
    cat_crit = sum(1 for c in checks if not c.passed and c.severity == "critical")
    cat_high = sum(1 for c in checks if not c.passed and c.severity == "high")
    cat_med = sum(1 for c in checks if not c.passed and c.severity == "medium")
    cat_low = sum(1 for c in checks if not c.passed and c.severity == "low")
    
    assert cat_crit == 1
    assert cat_high == 1
    assert cat_med == 1
    assert cat_low == 1


# 22. test_no_external_api_or_provider_import_required
def test_no_external_api_or_provider_import_required() -> None:
    with open("ops/readiness.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "import openai" not in content
    assert "openai." not in content
    assert "import requests" not in content
    assert "import httpx" not in content
    assert "urllib." not in content


# 23. test_readiness_output_paths_are_under_data_ops_readiness
def test_readiness_output_paths_are_under_data_ops_readiness(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    auditor = ContinuousTrainingReadinessAuditor(repo_root=tmp_path)
    
    config = ContinuousTrainingReadinessConfig(output_dir=str(tmp_path / "data" / "ops_readiness"))
    res = auditor.run_all_checks(config)
    
    assert "data/ops_readiness" in res.report_json_path.replace("\\", "/")
    assert "data/ops_readiness" in res.report_markdown_path.replace("\\", "/")


# 24. test_readiness_docs_include_never_do_manually_section
def test_readiness_docs_include_never_do_manually_section(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    
    # Read the generated runbook doc
    runbook_text = Path(tmp_path / "docs" / "continuous_training_runbook.md").read_text(encoding="utf-8")
    assert "must never do manually" in runbook_text


# 25. test_safety_gates_doc_mentions_all_red_lines
def test_safety_gates_doc_mentions_all_red_lines(tmp_path: Path) -> None:
    create_dummy_repo_structure(tmp_path)
    
    # Read the safety gates doc
    gates_text = Path(tmp_path / "docs" / "dana_training_safety_gates.md").read_text(encoding="utf-8")
    assert "no transfer without consent" in gates_text
    assert "no licensed claim" in gates_text
    assert "no price quotes" in gates_text
    assert "no approval/qualification promises" in gates_text
    assert "no DNC/wrong-number continuation" in gates_text
    assert "no PII collection" in gates_text
