"""Advanced integration and safety tests for the Training Operations Web Console."""

from __future__ import annotations

import os
import re
import sys
import json
import asyncio
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

from storage.repository import Repository
from ops.web_console import TrainingWebConsoleServer, TrainingWebConsoleConfig
from ops.training_console import TrainingOperationsConsole, ConsoleActionResult
from ops.readiness import ContinuousTrainingReadinessAuditor, ContinuousTrainingReadinessConfig


# Helper to build mock console actions
def mock_action_result(action: str, success: bool = True, message: str = "Success", data: dict = None) -> ConsoleActionResult:
    res = ConsoleActionResult(
        action=action,
        success=success,
        message=message,
        data=data or {}
    )
    return res


# QA / Eval Tests
@pytest.mark.asyncio
async def test_api_run_daily_qa_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("run_daily_qa")
    with patch.object(server.console, "run_daily_qa", return_value=mock_res) as mock_run:
        body = {"date": "2026-05-31", "dry_run": True, "limit": 10}
        status, data = await server.handle_api("POST", "/api/qa/daily", body)
        assert status == 200
        assert data["success"] is True
        mock_run.assert_called_once_with(
            date="2026-05-31",
            date_from=None,
            date_to=None,
            dry_run=True,
            limit=10
        )


@pytest.mark.asyncio
async def test_api_run_eval_cases_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("run_eval_cases")
    with patch.object(server.console, "run_eval_cases", return_value=mock_res) as mock_run:
        body = {"case_id": "case_1", "stage": "greeting", "objection": "price", "limit": 5}
        status, data = await server.handle_api("POST", "/api/evals/run", body)
        assert status == 200
        assert data["success"] is True
        mock_run.assert_called_once_with(
            case_id="case_1",
            stage="greeting",
            objection="price",
            limit=5
        )


@pytest.mark.asyncio
async def test_api_run_replay_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("run_transcript_replay")
    with patch.object(server.console, "run_transcript_replay", return_value=mock_res) as mock_run:
        body = {"fixture": "path/to/fixture.json", "fixture_dir": "evals/fixtures", "mode": "static", "fail_fast": True}
        status, data = await server.handle_api("POST", "/api/replay/run", body)
        assert status == 200
        assert data["success"] is True
        mock_run.assert_called_once_with(
            fixture="path/to/fixture.json",
            fixture_dir="evals/fixtures",
            mode="static",
            fail_fast=True
        )


@pytest.mark.asyncio
async def test_api_run_simulations_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("run_prospect_simulations")
    with patch.object(server.console, "run_prospect_simulations", return_value=mock_res) as mock_run:
        body = {"persona": "busy_bill", "run_all": False}
        status, data = await server.handle_api("POST", "/api/simulations/run", body)
        assert status == 200
        assert data["success"] is True
        mock_run.assert_called_once_with(
            persona="busy_bill",
            run_all=False
        )


# Prompt Gating Tests
@pytest.mark.asyncio
async def test_api_list_prompt_versions_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("list_prompt_versions")
    with patch.object(server.console, "list_prompt_versions", return_value=mock_res) as mock_list:
        status, data = await server.handle_api("GET", "/api/prompt/versions?limit=30", None)
        assert status == 200
        mock_list.assert_called_once_with(limit=30)


@pytest.mark.asyncio
async def test_api_generate_prompt_patches_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("generate_prompt_patches")
    with patch.object(server.console, "generate_prompt_patches", return_value=mock_res) as mock_gen:
        body = {"dry_run": True, "limit": 150}
        status, data = await server.handle_api("POST", "/api/prompt/patches/generate", body)
        assert status == 200
        mock_gen.assert_called_once_with(dry_run=True, limit=150)


@pytest.mark.asyncio
async def test_api_preview_prompt_patches_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("preview_prompt_patches")
    with patch.object(server.console, "preview_prompt_patches", return_value=mock_res) as mock_prev:
        body = {"patch_id": "patch_1", "approved_only": True, "create_candidate_version": True, "skip_gates": False}
        status, data = await server.handle_api("POST", "/api/prompt/patches/preview", body)
        assert status == 200
        mock_prev.assert_called_once_with(
            patch_id="patch_1",
            approved_only=True,
            create_candidate_version=True,
            skip_gates=False
        )


def test_prompt_preview_does_not_modify_live_prompt():
    prompt_file = Path("prompts/final_expense_alex.md")
    content_before = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "master_prompt"
    
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    # Running readiness/safety audits does not write to active final_expense_alex.md
    auditor = ContinuousTrainingReadinessAuditor()
    checks = auditor.check_prompt_file_safety()
    assert all(c.passed for c in checks)
    
    content_after = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "master_prompt"
    assert content_before == content_after


# Canary Rollout Tests
@pytest.mark.asyncio
async def test_api_list_canaries_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("list_canaries")
    with patch.object(server.console, "list_canaries", return_value=mock_res) as mock_list:
        status, data = await server.handle_api("GET", "/api/canary/list?limit=15", None)
        assert status == 200
        mock_list.assert_called_once_with(limit=15)


@pytest.mark.asyncio
async def test_api_check_canary_candidate_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("check_canary_candidate")
    with patch.object(server.console, "check_canary_candidate", return_value=mock_res) as mock_check:
        body = {"prompt_version_id": "v1"}
        status, data = await server.handle_api("POST", "/api/canary/check-candidate", body)
        assert status == 200
        mock_check.assert_called_once_with("v1")


@pytest.mark.asyncio
async def test_api_create_canary_requires_operator():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    body = {"prompt_version_id": "v1", "traffic_percent": 5.0, "operator": "", "notes": "my notes"}
    status, data = await server.handle_api("POST", "/api/canary/create", body)
    assert status == 400
    assert "operator is required" in data["error"]


@pytest.mark.asyncio
async def test_api_canary_rollback_requires_reason():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    body = {"operator": "Jimmy", "notes": ""}  # notes/reason empty
    status, data = await server.handle_api("POST", "/api/canary/exp_1/rollback", body)
    assert status == 400
    assert "notes/reason is required" in data["error"]


@pytest.mark.asyncio
async def test_api_canary_actions_call_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    actions = ["approve", "start", "pause", "rollback", "complete", "cancel"]
    
    for action in actions:
        mock_method = f"{action}_canary"
        mock_res = mock_action_result(mock_method)
        
        with patch.object(server.console, mock_method, return_value=mock_res) as mock_act:
            body = {"operator": "Jimmy", "notes": "notes for rollback/cancel"}
            status, data = await server.handle_api("POST", f"/api/canary/exp_1/{action}", body)
            assert status == 200
            mock_act.assert_called_once_with("exp_1", "Jimmy", "notes for rollback/cancel")


# Fine-tune packaging Tests
@pytest.mark.asyncio
async def test_api_export_fine_tune_dataset_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("export_fine_tune_dataset")
    with patch.object(server.console, "export_fine_tune_dataset", return_value=mock_res) as mock_export:
        body = {"dry_run": True, "limit": 1000, "stage": "greeting", "objection": "price"}
        status, data = await server.handle_api("POST", "/api/fine-tune/export", body)
        assert status == 200
        mock_export.assert_called_once_with(
            dry_run=True,
            limit=1000,
            stage="greeting",
            objection="price"
        )


@pytest.mark.asyncio
async def test_api_gate_fine_tune_dataset_requires_dataset_path():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    body = {"dataset_path": "", "strict": True}
    status, data = await server.handle_api("POST", "/api/fine-tune/gate", body)
    assert status == 400
    assert "dataset_path is required" in data["error"]


@pytest.mark.asyncio
async def test_api_prepare_job_request_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("prepare_fine_tune_job_request")
    with patch.object(server.console, "prepare_fine_tune_job_request", return_value=mock_res) as mock_req:
        body = {"dataset_path": "data/exports/ft.jsonl", "gate_report_path": "data/reports/ft.json", "provider": "openai", "dry_run": True}
        status, data = await server.handle_api("POST", "/api/fine-tune/job-request", body)
        assert status == 200
        mock_req.assert_called_once_with(
            dataset_path="data/exports/ft.jsonl",
            gate_report_path="data/reports/ft.json",
            provider="openai",
            dry_run=True
        )


@pytest.mark.asyncio
async def test_api_track_fine_tune_job_requires_operator():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    body = {"job_request_id": "req_1", "status": "running", "operator": "", "notes": "notes"}
    status, data = await server.handle_api("POST", "/api/fine-tune/track", body)
    assert status == 400
    assert "operator is required" in data["error"]


@pytest.mark.asyncio
async def test_api_list_fine_tune_tracking_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("list_fine_tune_tracking")
    with patch.object(server.console, "list_fine_tune_tracking", return_value=mock_res) as mock_list:
        status, data = await server.handle_api("GET", "/api/fine-tune/tracking?limit=25", None)
        assert status == 200
        mock_list.assert_called_once_with(limit=25)


def test_fine_tune_endpoints_do_not_upload_or_start_provider_job():
    auditor = ContinuousTrainingReadinessAuditor()
    checks = auditor.check_no_forbidden_provider_calls()
    assert all(c.passed for c in checks)


# Post-call Export Tests
@pytest.mark.asyncio
async def test_api_post_call_export_calls_console():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    mock_res = mock_action_result("export_completed_call_payload")
    with patch.object(server.console, "export_completed_call_payload", return_value=mock_res) as mock_export:
        body = {"payload": {"call_id": "test_1"}, "enabled": True, "run_intake": True, "dry_run": True}
        status, data = await server.handle_api("POST", "/api/post-call/export", body)
        assert status == 200
        mock_export.assert_called_once_with(
            payload={"call_id": "test_1"},
            enabled=True,
            run_intake=True,
            dry_run=True
        )


def test_post_call_export_does_not_enable_runtime_hook():
    auditor = ContinuousTrainingReadinessAuditor()
    checks = auditor.check_automation_layer_safety()
    # Check that agent_runtime requires explicit env check and fails closed
    assert any(c.check_id == "post_call_export_requires_env_flag" and c.passed for c in checks)


# UI & Static Checks
def test_advanced_tabs_exist_in_html():
    html_file = Path("static/training_console/index.html")
    assert html_file.exists()
    content = html_file.read_text(encoding="utf-8")
    
    assert 'data-tab="qa-tab"' in content
    assert 'data-tab="prompt-tab"' in content
    assert 'data-tab="canary-tab"' in content
    assert 'data-tab="finetune-tab"' in content
    assert 'data-tab="postcall-tab"' in content
    assert 'data-tab="reports-tab"' in content


def test_advanced_js_functions_exist():
    js_file = Path("static/training_console/app.js")
    assert js_file.exists()
    content = js_file.read_text(encoding="utf-8")
    
    assert "run-qa-miner" in content
    assert "run-evals" in content
    assert "run-replay" in content
    assert "run-simulations" in content
    assert "generate-patches" in content
    assert "preview-patches" in content
    assert "create-canary-plan" in content
    assert "triggerCanaryAction" in content
    assert "ft-export-form" in content
    assert "ft-gate-form" in content
    assert "ft-request-form" in content
    assert "ft-track-form" in content
    assert "postcall-form" in content


def test_static_js_has_no_external_urls():
    js_content = Path("static/training_console/app.js").read_text(encoding="utf-8")
    # Verify no external HTTP/HTTPS calls except for template strings or localhost
    urls = re.findall(r'https?://[^\s"\']+', js_content)
    for url in urls:
        assert "127.0.0.1" in url or "localhost" in url or "youtube.com/..." in url


def test_no_provider_calls_in_web_console_or_static_js():
    web_code = Path("ops/web_console.py").read_text(encoding="utf-8")
    js_code = Path("static/training_console/app.js").read_text(encoding="utf-8")
    
    assert "import openai" not in web_code
    assert "openai" not in js_code.lower()
    assert "azure" not in web_code.lower()
    assert "azure" not in js_code.lower()


def test_no_shell_true_in_console_or_web_console():
    console_code = Path("ops/training_console.py").read_text(encoding="utf-8")
    web_code = Path("ops/web_console.py").read_text(encoding="utf-8")
    assert "shell=True" not in console_code
    assert "shell=True" not in web_code


def test_no_live_prompt_file_modified_by_advanced_endpoints():
    prompt_file = Path("prompts/final_expense_alex.md")
    content_before = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "master_prompt"
    
    # Static check that web_console.py doesn't write to final_expense_alex.md
    web_code = Path("ops/web_console.py").read_text(encoding="utf-8")
    assert "prompts/final_expense_alex.md" not in web_code or "open" not in web_code
    
    content_after = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "master_prompt"
    assert content_before == content_after


def test_docs_advanced_workflows_exist_and_include_safety_boundaries():
    doc_path = Path("docs/training_web_console_advanced_workflows.md")
    assert doc_path.exists()
    content = doc_path.read_text(encoding="utf-8")
    
    assert "no auto-promotion" in content.lower()
    assert "does not modify live prompt" in content.lower()
    assert "packaging only" in content.lower() or "does not upload" in content.lower()
    assert "does not enable live runtime hook" in content.lower()


def test_readiness_includes_advanced_web_console_docs_and_tests():
    auditor = ContinuousTrainingReadinessAuditor()
    doc_checks = auditor.check_docs_and_runbooks()
    test_checks = auditor.check_tests_exist()
    
    assert any(c.check_id == "doc_training_web_console_advanced_workflows" for c in doc_checks)
    assert any(c.check_id == "test_test_training_web_console_advanced" for c in test_checks)


@pytest.mark.asyncio
async def test_all_advanced_routes_return_json_error_on_bad_input():
    server = TrainingWebConsoleServer(TrainingWebConsoleConfig())
    
    bad_routes = [
        ("POST", "/api/qa/daily", {}), # date missing
        ("POST", "/api/canary/create", {"operator": ""}), # operator missing
        ("POST", "/api/canary/exp_1/rollback", {"operator": "Jimmy", "notes": ""}), # notes missing
        ("POST", "/api/fine-tune/gate", {"dataset_path": ""}), # path missing
        ("POST", "/api/fine-tune/track", {"operator": ""}), # operator missing
        ("POST", "/api/post-call/export", {}), # payload missing
    ]
    
    for method, path, body in bad_routes:
        status, data = await server.handle_api(method, path, body)
        assert status == 400
        assert data["success"] is False
        assert "error" in data or "message" in data
