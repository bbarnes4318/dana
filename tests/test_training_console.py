"""Tests for Training Operations Console and CLI wrapper script."""

from __future__ import annotations

import os
import sys
import json
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock, patch

from storage.repository import Repository
from ops.training_console import (
    TrainingOperationsConsole,
    TrainingConsoleConfig,
    ConsoleActionResult,
    TrainingConsoleSummary,
)
from training.review_service import HumanReviewService, ReviewActionResult
from training.intake_orchestrator import TrainingIntakeOrchestrator, TrainingIntakeRunResult
from training.youtube_importer import YouTubeTranscriptImporter, YouTubeTranscriptImportResult
from training.intake_scheduler import TrainingIntakeScheduler, TrainingIntakeScheduleRun


@pytest.fixture
def temp_dir(tmp_path):
    """Return a temporary directory."""
    return tmp_path


@pytest.fixture
def repo(temp_dir):
    """Return a Repository backed by a temporary JSONL store."""
    return Repository(data_dir=temp_dir)


@pytest.fixture
def console(repo):
    """Return a TrainingOperationsConsole using a temporary Repository."""
    return TrainingOperationsConsole(repository=repo)


# 1. test_console_summary_returns_counts
@pytest.mark.asyncio
async def test_console_summary_returns_counts(console, repo):
    """Verify that get_summary aggregates counts and pending_by_type properly."""
    # Create sample TrainingSource
    await repo.save_training_source(
        source_uri="http://example.com/source",
        title="Test Source",
        source_type="call_transcript",
        source_metadata={"campaign": "FE"},
        status="ingested",
        raw_payload={"dialog": []}
    )
    # Create sample HumanReviewItems
    await repo.save_human_review_item(
        item_type="training_example",
        payload={"text": "example 1"},
        status="pending"
    )
    await repo.save_human_review_item(
        item_type="eval_case",
        payload={"text": "eval 1"},
        status="pending"
    )
    await repo.save_human_review_item(
        item_type="training_example",
        payload={"text": "example 2"},
        status="approved"
    )

    summary = await console.get_summary()
    assert isinstance(summary, TrainingConsoleSummary)
    assert summary.pending_review_items == 2
    assert summary.pending_by_type == {"training_example": 1, "eval_case": 1}
    assert summary.recent_training_sources == 1
    assert summary.recent_human_review_items == 3


# 2. test_list_review_items_pending
@pytest.mark.asyncio
async def test_list_review_items_pending(console, repo):
    """Verify that listing review items with status='pending' returns pending only."""
    pending_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={"text": "pending"},
        status="pending"
    )
    approved_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={"text": "approved"},
        status="approved"
    )

    res = await console.list_review_items(status="pending")
    assert res.success is True
    items = res.data["items"]
    assert len(items) == 1
    assert items[0]["id"] == pending_id


# 3. test_show_review_item
@pytest.mark.asyncio
async def test_show_review_item(console, repo):
    """Verify that show_review_item returns the review item details."""
    item_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={"text": "show_me"},
        status="pending"
    )

    res = await console.show_review_item(item_id)
    assert res.success is True
    item = res.data["item"]
    assert item["id"] == item_id
    assert item["payload"]["text"] == "show_me"


# 4. test_approve_review_item_uses_review_service
@pytest.mark.asyncio
async def test_approve_review_item_uses_review_service(console):
    """Verify that approval action calls HumanReviewService.approve_review_item."""
    mock_res = ReviewActionResult(
        item_id="item_123",
        item_type="training_example",
        previous_status="pending",
        new_status="approved",
        reviewer="Jimmy",
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        created_record_type="training_example",
        created_record_id="rec_123",
        message="Approved successfully",
        warnings=[]
    )
    with patch("ops.training_console.HumanReviewService.approve_review_item", return_value=mock_res) as mock_approve:
        res = await console.approve_review_item(item_id="item_123", reviewer="Jimmy", notes="Good turn.")
        assert res.success is True
        mock_approve.assert_called_once_with("item_123", "Jimmy", review_notes="Good turn.")


# 5. test_approve_requires_reviewer
@pytest.mark.asyncio
async def test_approve_requires_reviewer(console):
    """Verify that approval fails if reviewer is missing."""
    res = await console.approve_review_item(item_id="item_123", reviewer="")
    assert res.success is False
    assert "Reviewer is required" in res.message


# 6. test_reject_requires_notes
@pytest.mark.asyncio
async def test_reject_requires_notes(console):
    """Verify that rejection fails if review notes are missing."""
    res = await console.reject_review_item(item_id="item_123", reviewer="Jimmy", notes="")
    assert res.success is False
    assert "Notes are required for rejection" in res.message


# 7. test_request_changes_requires_notes
@pytest.mark.asyncio
async def test_request_changes_requires_notes(console):
    """Verify that requesting changes fails if notes are missing."""
    res = await console.request_review_changes(item_id="item_123", reviewer="Jimmy", notes="")
    assert res.success is False
    assert "Notes are required for change requests" in res.message


# 8. test_run_folder_intake_calls_orchestrator
@pytest.mark.asyncio
async def test_run_folder_intake_calls_orchestrator(console):
    """Verify that folder intake calls TrainingIntakeOrchestrator.run."""
    mock_res = TrainingIntakeRunResult(
        run_id="run_123",
        mode="folder",
        started_at=datetime.now(timezone.utc).isoformat(),
        ended_at=datetime.now(timezone.utc).isoformat(),
        sources_discovered=2,
        sources_ingested=2,
        sources_failed=0,
        examples_mined=5,
        review_items_created=5,
        dry_run=False
    )
    with patch("ops.training_console.TrainingIntakeOrchestrator.run", return_value=mock_res) as mock_run:
        res = await console.run_intake(mode="folder", path="data/imports/call_transcripts", source_type="call_transcript")
        assert res.success is True
        mock_run.assert_called_once()


# 9. test_import_youtube_calls_importer
@pytest.mark.asyncio
async def test_import_youtube_calls_importer(console):
    """Verify that YouTube import calls YouTubeTranscriptImporter.import_transcripts."""
    mock_res = YouTubeTranscriptImportResult(
        import_id="imp_123",
        dry_run=False,
        total_items=1,
        imported_count=1,
        skipped_count=0,
        failed_count=0,
        warnings=[]
    )
    with patch("ops.training_console.YouTubeTranscriptImporter.import_transcripts", return_value=mock_res) as mock_import:
        res = await console.import_youtube(file="data/video.txt", title="FE Guide")
        assert res.success is True
        mock_import.assert_called_once()


# 10. test_run_scheduler_once_bounded
@pytest.mark.asyncio
async def test_run_scheduler_once_bounded(console):
    """Verify that scheduler once executes exactly 1 run limit and mode once."""
    mock_res = TrainingIntakeScheduleRun(
        scheduler_run_id="run_123",
        mode="once",
        started_at=datetime.now(timezone.utc).isoformat(),
        ended_at=datetime.now(timezone.utc).isoformat(),
        max_runs=1,
        completed_runs=1,
        successful_runs=1,
        failed_runs=0,
        dry_run=False,
        warnings=[]
    )
    with patch("ops.training_console.TrainingIntakeScheduler.run", return_value=mock_res) as mock_run:
        res = await console.run_scheduler_once()
        assert res.success is True
        mock_run.assert_called_once()
        config = mock_run.call_args[0][0]
        assert config.mode == "once"
        assert config.max_runs == 1


# 11. test_run_readiness_returns_result
@pytest.mark.asyncio
async def test_run_readiness_returns_result(console):
    """Verify that readiness audit execution returns ConsoleActionResult summary."""
    mock_res = MagicMock()
    mock_res.passed = True
    mock_res.total_checks = 5
    mock_res.checks_passed = 5
    mock_res.checks_failed = 0
    mock_res.critical_failures = 0
    mock_res.high_failures = 0
    mock_res.medium_warnings = 0
    mock_res.low_warnings = 0
    mock_res.category_results = []
    mock_res.safety_summary = {}
    mock_res.missing_components = []
    mock_res.remediation_items = []
    mock_res.report_json_path = "json_path"
    mock_res.report_markdown_path = "md_path"
    mock_res.model_dump.return_value = {"passed": True}

    with patch("ops.training_console.ContinuousTrainingReadinessAuditor.run_all_checks", return_value=mock_res) as mock_run:
        res = console.run_readiness(strict=True)
        assert res.success is True
        mock_run.assert_called_once()


# 12. test_list_reports_reads_safe_report_dirs
@pytest.mark.asyncio
async def test_list_reports_reads_safe_report_dirs(console, temp_dir):
    """Verify that report lists are read and sorted cleanly from safe directories."""
    fake_reports_dir = temp_dir / "data" / "ops_readiness"
    fake_reports_dir.mkdir(parents=True, exist_ok=True)
    fake_file = fake_reports_dir / "readiness_test_report.json"
    fake_file.write_text("{}", encoding="utf-8")

    with patch("ops.training_console.Path") as mock_path:
        mock_instance = MagicMock()
        mock_instance.parent.parent.resolve.return_value = temp_dir
        
        from pathlib import Path as RealPath
        def side_effect(*args, **kwargs):
            if args and "training_console" in str(args[0]):
                return mock_instance
            return RealPath(*args, **kwargs)
        mock_path.side_effect = side_effect

        res = console.list_reports(report_type="readiness")
        assert res.success is True
        reports = res.data["reports"]
        assert len(reports) > 0
        assert reports[0]["name"] == "readiness_test_report.json"


# 13. test_read_report_blocks_path_traversal
@pytest.mark.asyncio
async def test_read_report_blocks_path_traversal(console):
    """Verify that read_report blocks path traversal outside the repository root."""
    res = console.read_report(path="../../etc/passwd")
    assert res.success is False
    assert "Access denied" in res.error or "outside the repository" in res.error


# 14. test_cli_summary_outputs_json
def test_cli_summary_outputs_json(temp_dir):
    """Verify execution of CLI summary subcommand and assert valid stdout JSON."""
    cmd = [
        sys.executable,
        "scripts/training_console.py",
        "summary"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(temp_dir)
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert "pending_review_items" in data


# 15. test_cli_review_list_outputs_json
def test_cli_review_list_outputs_json(temp_dir):
    """Verify execution of CLI review list subcommand and assert valid stdout JSON."""
    cmd = [
        sys.executable,
        "scripts/training_console.py",
        "review", "list",
        "--status", "pending"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(temp_dir)
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    data = json.loads(res.stdout.strip())
    assert data["success"] is True
    assert "items" in data["data"]


# 16. test_cli_readiness_outputs_json
def test_cli_readiness_outputs_json(temp_dir):
    """Verify execution of CLI readiness subcommand and assert valid stdout JSON."""
    cmd = [
        sys.executable,
        "scripts/training_console.py",
        "readiness"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(temp_dir)
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode in (0, 1)
    data = json.loads(res.stdout.strip())
    assert "success" in data
    assert data["action"] == "run_readiness"


# 17. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(console):
    """Verify that prompt files are not modified during normal console actions."""
    prompt_file = Path("prompts/final_expense_alex.md")
    content_before = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "dummy"

    await console.get_summary()
    await console.list_review_items()
    console.run_readiness(strict=False)

    content_after = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "dummy"
    assert content_before == content_after


# 18. test_no_external_api_or_provider_calls
def test_no_external_api_or_provider_calls():
    """Verify console and scripts do not import or execute forbidden provider SDKs."""
    console_code = Path("ops/training_console.py").read_text(encoding="utf-8")
    cli_code = Path("scripts/training_console.py").read_text(encoding="utf-8")

    for code in (console_code, cli_code):
        assert "import openai" not in code
        assert "openai." not in code
        assert "import requests" not in code
        assert "import httpx" not in code
        assert "azure" not in code.lower()


# 19. test_no_auto_approval_without_explicit_action
@pytest.mark.asyncio
async def test_no_auto_approval_without_explicit_action(console, repo):
    """Verify that general console read actions do not alter pending item statuses."""
    item_id = await repo.save_human_review_item(
        item_type="training_example",
        payload={"text": "check auto approve"},
        status="pending"
    )

    await console.get_summary()
    await console.list_review_items()

    item = await repo.get_human_review_item(item_id)
    assert item["status"] == "pending"


# 20. test_reject_uses_review_service_not_direct_status_write
@pytest.mark.asyncio
async def test_reject_uses_review_service_not_direct_status_write(console):
    """Verify that reject_review_item uses HumanReviewService logic rather than a direct status update."""
    mock_res = ReviewActionResult(
        item_id="item_123",
        item_type="training_example",
        previous_status="pending",
        new_status="rejected",
        reviewer="Jimmy",
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        created_record_type=None,
        created_record_id=None,
        message="Rejected successfully",
        warnings=[]
    )
    with patch("ops.training_console.HumanReviewService.reject_review_item", return_value=mock_res) as mock_reject:
        res = await console.reject_review_item(item_id="item_123", reviewer="Jimmy", notes="Unsafe.")
        assert res.success is True
        mock_reject.assert_called_once_with("item_123", "Jimmy", review_notes="Unsafe.")


# 21. test_console_result_json_serializable
@pytest.mark.asyncio
async def test_console_result_json_serializable(console, repo):
    """Verify that ConsoleActionResult and summary serialize cleanly to JSON."""
    summary = await console.get_summary()
    assert json.loads(json.dumps(summary.model_dump(mode="json"))) == summary.model_dump(mode="json")

    res = await console.list_review_items()
    assert json.loads(json.dumps(res.model_dump(mode="json"))) == res.model_dump(mode="json")


# 22. test_docs_exist_and_include_safety_limits
def test_docs_exist_and_include_safety_limits():
    """Verify that the console documentation exists and contains the safety guidelines."""
    doc_path = Path("docs/training_operations_console.md")
    assert doc_path.exists()
    content = doc_path.read_text(encoding="utf-8")
    assert "no auto-approval" in content.lower()
    assert "no live prompt" in content.lower()
    assert "no provider upload" in content.lower()
    assert "no fine-tune" in content.lower()
    assert "no live deployment" in content.lower()
