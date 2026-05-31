"""Intake Scheduler for Dana's continuous training system.

Runs the training intake orchestrator loop safely on a schedule.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field

from storage.repository import Repository
from training.intake_orchestrator import TrainingIntakeOrchestrator, TrainingIntakeConfig


class TrainingIntakeScheduleConfig(BaseModel):
    """Configuration for running the intake scheduler."""

    mode: Literal["once", "daily", "hourly"] = "once"
    output_dir: str = "data/intake_reports"
    state_path: str = "data/intake_reports/intake_state.json"
    folders: list[str] = Field(default_factory=lambda: [
        "data/imports/call_transcripts",
        "data/imports/youtube_training",
        "data/imports/manager_notes",
        "data/imports/licensed_agent_feedback",
        "data/imports/post_call_payloads",
    ])
    run_daily_qa: bool = True
    label_after_ingest: bool = True
    mine_after_label: bool = True
    continue_on_error: bool = True
    limit: Optional[int] = None
    dry_run: bool = False
    sleep_seconds: Optional[float] = None
    max_runs: int = 1
    since: Optional[str] = None
    lock_path: str = "data/intake_reports/intake_scheduler.lock"
    report_markdown: bool = True


class TrainingIntakeScheduleRun(BaseModel):
    """Execution status and statistics of a scheduler run."""

    scheduler_run_id: str
    mode: str
    started_at: str
    ended_at: Optional[str] = None
    max_runs: int
    completed_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    intake_results: list[dict[str, Any]] = Field(default_factory=list)
    lock_acquired: bool = False
    lock_path: Optional[str] = None
    dry_run: bool
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    report_json_path: Optional[str] = None
    report_markdown_path: Optional[str] = None


class TrainingIntakeScheduler:
    """Manages scheduling loops and process exclusion locks for training intake runs."""

    def __init__(self, repository: Repository | None = None, orchestrator: TrainingIntakeOrchestrator | None = None) -> None:
        self.repository = repository or Repository()
        self.orchestrator = orchestrator or TrainingIntakeOrchestrator(repository=self.repository)
        self._lock_file_acquired = False

    def _is_pid_running(self, pid: int) -> bool:
        """Determines if a given PID is currently active on the host OS."""
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            else:
                return True

    def acquire_lock(self, lock_path: str | Path) -> bool:
        """Acquires the file lock. Returns True if successfully acquired, else False."""
        p = Path(lock_path)
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8").strip()
                pid = int(content)
                if self._is_pid_running(pid):
                    return False
            except Exception:
                # Invalid or stale content, overwrite
                pass

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(os.getpid()), encoding="utf-8")
            self._lock_file_acquired = True
            return True
        except Exception:
            return False

    def release_lock(self, lock_path: str | Path) -> None:
        """Releases the file lock if it was acquired by this process."""
        p = Path(lock_path)
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8").strip()
                pid = int(content)
                if pid == os.getpid():
                    p.unlink(missing_ok=True)
            except Exception:
                p.unlink(missing_ok=True)
        self._lock_file_acquired = False

    def build_intake_config(self, config: TrainingIntakeScheduleConfig) -> TrainingIntakeConfig:
        """Constructs an orchestrator configuration dictionary from scheduler settings."""
        return TrainingIntakeConfig(
            mode="daily",  # Daily mode runs directory scans and triggers Daily QA Miner
            folders=config.folders,
            output_dir=config.output_dir,
            state_path=config.state_path,
            label_after_ingest=config.label_after_ingest,
            mine_after_label=config.mine_after_label,
            run_daily_qa=config.run_daily_qa,
            continue_on_error=config.continue_on_error,
            limit=config.limit,
            dry_run=config.dry_run,
            since=config.since,
            report_markdown=config.report_markdown,
        )

    async def run_once(self, config: TrainingIntakeScheduleConfig) -> dict[str, Any]:
        """Executes a single intake run using the orchestrator."""
        intake_cfg = self.build_intake_config(config)
        res = await self.orchestrator.run(intake_cfg)
        return res.model_dump(mode="json")

    async def run(self, config: TrainingIntakeScheduleConfig) -> TrainingIntakeScheduleRun:
        """Starts the scheduler execution loop, respecting locks and max runs limits."""
        started_at = datetime.now(timezone.utc).isoformat()
        scheduler_run_id = f"scheduler_{uuid.uuid4().hex[:8]}"
        
        run_res = TrainingIntakeScheduleRun(
            scheduler_run_id=scheduler_run_id,
            mode=config.mode,
            started_at=started_at,
            max_runs=config.max_runs,
            dry_run=config.dry_run,
            lock_path=str(Path(config.lock_path).resolve()).replace("\\", "/"),
        )

        # 1. Process exclusion lock
        if not config.dry_run:
            if not self.acquire_lock(config.lock_path):
                run_res.error = "Lock conflict"
                run_res.warnings.append("Scheduler lock already exists.")
                run_res.ended_at = datetime.now(timezone.utc).isoformat()
                return run_res
            run_res.lock_acquired = True

        try:
            completed_runs = 0
            successful_runs = 0
            failed_runs = 0
            
            while completed_runs < config.max_runs:
                try:
                    result = await self.run_once(config)
                    run_res.intake_results.append(result)
                    successful_runs += 1
                except Exception as loop_err:
                    failed_runs += 1
                    run_res.warnings.append(f"Intake iteration {completed_runs + 1} failed: {loop_err}")
                    if not config.continue_on_error:
                        raise loop_err
                
                completed_runs += 1
                
                # Check sleep if running loop
                if completed_runs < config.max_runs:
                    sleep_time = config.sleep_seconds
                    if sleep_time is None:
                        sleep_time = 3600 if config.mode == "hourly" else 86400
                    await asyncio.sleep(sleep_time)

            run_res.completed_runs = completed_runs
            run_res.successful_runs = successful_runs
            run_res.failed_runs = failed_runs

        except Exception as run_err:
            run_res.error = str(run_err)
            run_res.warnings.append(f"Scheduler execution crashed: {run_err}")

        finally:
            # 2. Release lock
            if run_res.lock_acquired:
                self.release_lock(config.lock_path)
                run_res.lock_acquired = False

        run_res.ended_at = datetime.now(timezone.utc).isoformat()

        # Write reports
        if config.output_dir:
            json_p, md_p = self.write_schedule_report(run_res, config.output_dir)
            run_res.report_json_path = json_p
            run_res.report_markdown_path = md_p

        return run_res

    def write_schedule_report(self, result: TrainingIntakeScheduleRun, output_dir: str | Path) -> tuple[str, str]:
        """Generates scheduler JSON and Markdown report summaries."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        json_file = out / f"intake_scheduler_{result.scheduler_run_id}.json"
        md_file = out / f"intake_scheduler_{result.scheduler_run_id}.md"

        # JSON
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(mode="json"), f, indent=2)

        # Markdown
        # Sum up underlying orchestrator statistics
        total_ingested = 0
        total_duplicates = 0
        total_skipped = 0
        total_failed = 0
        total_review_items = 0
        
        for r in result.intake_results:
            total_ingested += r.get("ingested_count", 0)
            total_duplicates += r.get("duplicate_count", 0)
            total_skipped += r.get("skipped_count", 0)
            total_failed += r.get("failed_count", 0)
            total_review_items += r.get("review_items_created", 0)

        md_content = f"""# Dana Training Intake Scheduler Report

Scheduler Run ID: {result.scheduler_run_id}
Schedule Mode: {result.mode}
Started: {result.started_at}
Ended: {result.ended_at or "N/A"}
Dry run: {result.dry_run}

## Executive Summary
- Successful runs completed: {result.successful_runs} / {result.max_runs}
- Failed iterations: {result.failed_runs}
- Total ingested items: {total_ingested}
- Duplicates detected: {total_duplicates}
- Skipped (via state check): {total_skipped}
- Ingestion failures: {total_failed}
- Human Review items generated: {total_review_items}

## Run Iteration Log
| Iteration | Status | Ingested | Duplicates | Skipped | Failed | Review Items Created | Warnings |
| --- | --- | --- | --- | --- | --- | --- | --- |
"""
        for idx, r in enumerate(result.intake_results):
            md_content += f"| {idx + 1} | SUCCESS | {r.get('ingested_count')} | {r.get('duplicate_count')} | {r.get('skipped_count')} | {r.get('failed_count')} | {r.get('review_items_created')} | None |\n"

        if result.error:
            md_content += f"\n### Critical Error\n- **Error**: {result.error}\n"

        md_content += """
## Safety Notes
- **No auto-approval performed**: All mined training examples and coaching lessons are in a pending state.
- **No prompt edits performed**: The live prompt configuration file is untouched.
- **No fine-tuning started**: Provider fine-tuning uploads or start endpoints were not invoked.
- **No provider API calls**: Scan and ingestion processes were run 100% offline.
- **Human review required**: Mined examples must be explicitly reviewed and approved by an administrator.

## Next Steps
1. Navigate to the HumanReviewItem coaching dashboard.
2. Review pending training example candidates.
3. Validate candidate models against offline eval/replay/simulation gates.
"""
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md_content)

        return (
            str(json_file.resolve()).replace("\\", "/"),
            str(md_file.resolve()).replace("\\", "/"),
        )
