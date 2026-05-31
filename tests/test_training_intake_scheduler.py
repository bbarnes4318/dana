import json
import os
import sys
import subprocess
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from storage.repository import Repository
from training.intake_scheduler import TrainingIntakeScheduler, TrainingIntakeScheduleConfig


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    return Repository(data_dir=tmp_path)


@pytest.fixture
def orchestrator_mock() -> MagicMock:
    mock_orch = MagicMock()
    mock_orch.run = AsyncMock(return_value=MagicMock(
        model_dump=lambda mode="json": {
            "run_id": "test_run",
            "ingested_count": 0,
            "duplicate_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "review_items_created": 0,
        }
    ))
    return mock_orch


@pytest.fixture
def scheduler(repo: Repository, orchestrator_mock: MagicMock) -> TrainingIntakeScheduler:
    return TrainingIntakeScheduler(repository=repo, orchestrator=orchestrator_mock)


# 1. test_scheduler_run_once_calls_orchestrator
@pytest.mark.asyncio
async def test_scheduler_run_once_calls_orchestrator(scheduler: TrainingIntakeScheduler, orchestrator_mock: MagicMock, tmp_path: Path):
    lock_file = tmp_path / "scheduler.lock"
    config = TrainingIntakeScheduleConfig(mode="once", lock_path=str(lock_file))
    
    res = await scheduler.run(config)
    assert res.completed_runs == 1
    assert res.successful_runs == 1
    assert orchestrator_mock.run.called


# 2. test_scheduler_daily_mode_respects_max_runs
@pytest.mark.asyncio
async def test_scheduler_daily_mode_respects_max_runs(scheduler: TrainingIntakeScheduler, tmp_path: Path):
    lock_file = tmp_path / "scheduler.lock"
    config = TrainingIntakeScheduleConfig(
        mode="daily",
        max_runs=2,
        sleep_seconds=0.001,  # Short sleep for testing
        lock_path=str(lock_file)
    )
    
    res = await scheduler.run(config)
    assert res.completed_runs == 2
    assert res.successful_runs == 2


# 3. test_scheduler_hourly_mode_respects_max_runs
@pytest.mark.asyncio
async def test_scheduler_hourly_mode_respects_max_runs(scheduler: TrainingIntakeScheduler, tmp_path: Path):
    lock_file = tmp_path / "scheduler.lock"
    config = TrainingIntakeScheduleConfig(
        mode="hourly",
        max_runs=3,
        sleep_seconds=0.001,
        lock_path=str(lock_file)
    )
    
    res = await scheduler.run(config)
    assert res.completed_runs == 3
    assert res.successful_runs == 3


# 4. test_scheduler_lock_prevents_double_run
@pytest.mark.asyncio
async def test_scheduler_lock_prevents_double_run(scheduler: TrainingIntakeScheduler, tmp_path: Path):
    lock_file = tmp_path / "scheduler.lock"
    config = TrainingIntakeScheduleConfig(mode="once", lock_path=str(lock_file))
    
    # Artificially acquire lock by writing a running PID
    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    
    res = await scheduler.run(config)
    assert res.lock_acquired is False
    assert "lock" in res.error.lower()
    assert len(res.warnings) > 0


# 5. test_scheduler_releases_lock_after_success
@pytest.mark.asyncio
async def test_scheduler_releases_lock_after_success(scheduler: TrainingIntakeScheduler, tmp_path: Path):
    lock_file = tmp_path / "scheduler.lock"
    config = TrainingIntakeScheduleConfig(mode="once", lock_path=str(lock_file))
    
    res = await scheduler.run(config)
    assert res.error is None
    assert not lock_file.exists()


# 6. test_scheduler_dry_run_passes_to_intake
@pytest.mark.asyncio
async def test_scheduler_dry_run_passes_to_intake(scheduler: TrainingIntakeScheduler, orchestrator_mock: MagicMock, tmp_path: Path):
    lock_file = tmp_path / "scheduler.lock"
    config = TrainingIntakeScheduleConfig(mode="once", dry_run=True, lock_path=str(lock_file))
    
    await scheduler.run(config)
    assert orchestrator_mock.run.called
    # Check config parameter passed to orchestrator
    run_args = orchestrator_mock.run.call_args[0][0]
    assert run_args.dry_run is True


# 7. test_scheduler_writes_reports
@pytest.mark.asyncio
async def test_scheduler_writes_reports(scheduler: TrainingIntakeScheduler, tmp_path: Path):
    lock_file = tmp_path / "scheduler.lock"
    report_dir = tmp_path / "reports"
    config = TrainingIntakeScheduleConfig(mode="once", lock_path=str(lock_file), output_dir=str(report_dir))
    
    res = await scheduler.run(config)
    assert res.report_json_path is not None
    assert res.report_markdown_path is not None
    assert Path(res.report_json_path).exists()
    assert Path(res.report_markdown_path).exists()


# 8. test_scheduler_report_contains_safety_notes
@pytest.mark.asyncio
async def test_scheduler_report_contains_safety_notes(scheduler: TrainingIntakeScheduler, tmp_path: Path):
    lock_file = tmp_path / "scheduler.lock"
    report_dir = tmp_path / "reports"
    config = TrainingIntakeScheduleConfig(mode="once", lock_path=str(lock_file), output_dir=str(report_dir))
    
    res = await scheduler.run(config)
    md_content = Path(res.report_markdown_path).read_text(encoding="utf-8")
    
    assert "No auto-approval performed" in md_content
    assert "No prompt edits performed" in md_content
    assert "No fine-tuning started" in md_content
    assert "No provider API calls" in md_content


# 9. test_cli_scheduler_outputs_json
def test_cli_scheduler_outputs_json(tmp_path: Path):
    lock_file = tmp_path / "scheduler.lock"
    report_dir = tmp_path / "reports"
    state_file = tmp_path / "intake_state.json"
    
    cmd = [
        sys.executable,
        "scripts/run_training_intake_scheduler.py",
        "--mode", "once",
        "--output-dir", str(report_dir),
        "--state-path", str(state_file),
        "--lock-path", str(lock_file),
        "--no-daily-qa",
        "--no-label",
        "--no-mine"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 0
    
    data = json.loads(res.stdout.strip())
    assert data["mode"] == "once"
    assert data["completed_runs"] == 1


# 10. test_cli_scheduler_lock_failure_outputs_json
def test_cli_scheduler_lock_failure_outputs_json(tmp_path: Path):
    lock_file = tmp_path / "scheduler.lock"
    report_dir = tmp_path / "reports"
    state_file = tmp_path / "intake_state.json"
    
    # Write PID to lock file
    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    
    cmd = [
        sys.executable,
        "scripts/run_training_intake_scheduler.py",
        "--mode", "once",
        "--output-dir", str(report_dir),
        "--state-path", str(state_file),
        "--lock-path", str(lock_file),
        "--no-daily-qa"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(".").resolve())
    env["DANA_DATA_DIR"] = str(tmp_path)
    
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert res.returncode == 1
    
    data = json.loads(res.stdout.strip())
    assert data["error"] == "Lock conflict"
    assert "lock" in data["warnings"][0].lower()


# 11. test_no_live_prompt_file_modified
@pytest.mark.asyncio
async def test_no_live_prompt_file_modified(scheduler: TrainingIntakeScheduler, tmp_path: Path):
    live_prompt = Path("prompts/final_expense_alex.md")
    content_before = live_prompt.read_text(encoding="utf-8")
    
    lock_file = tmp_path / "scheduler.lock"
    config = TrainingIntakeScheduleConfig(mode="once", lock_path=str(lock_file))
    await scheduler.run(config)
    
    content_after = live_prompt.read_text(encoding="utf-8")
    assert content_before == content_after


# 12. test_no_external_api_calls
def test_no_external_api_calls():
    with open("training/intake_scheduler.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "import openai" not in content
    assert "openai." not in content
    assert "import requests" not in content
    assert "import httpx" not in content


# 13. test_no_auto_approval_or_prompt_edits
@pytest.mark.asyncio
async def test_no_auto_approval_or_prompt_edits(repo: Repository, tmp_path: Path):
    # Verify that the scheduler does not auto-approve items
    # We can mock the orchestrator's run to return generated candidates count and verify
    # review service states.
    mock_orch = MagicMock()
    mock_orch.run = AsyncMock(return_value=MagicMock(
        model_dump=lambda mode="json": {
            "run_id": "test_run_auto_approve",
            "ingested_count": 1,
            "duplicate_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "review_items_created": 2,
        }
    ))
    
    scheduler = TrainingIntakeScheduler(repository=repo, orchestrator=mock_orch)
    lock_file = tmp_path / "scheduler.lock"
    config = TrainingIntakeScheduleConfig(mode="once", lock_path=str(lock_file))
    
    res = await scheduler.run(config)
    assert res.error is None
    
    # Query all human review items and make sure none are auto-approved
    items = await repo.query_human_review_items({})
    for item in items:
        assert item["status"] == "pending"
