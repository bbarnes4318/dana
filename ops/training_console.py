"""Training Operations Console for Dana's continuous training system.

Provides a unified operations interface and service layer for operators.
"""

from __future__ import annotations

import os
import json
import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Dict, List
from pydantic import BaseModel, Field

from storage.repository import Repository
from training.review_service import HumanReviewService, ReviewActionResult
from training.intake_orchestrator import TrainingIntakeOrchestrator, TrainingIntakeConfig, TrainingIntakeRunResult
from training.youtube_importer import YouTubeTranscriptImporter, YouTubeTranscriptImportConfig, YouTubeTranscriptImportResult
from training.intake_scheduler import TrainingIntakeScheduler, TrainingIntakeScheduleConfig, TrainingIntakeScheduleRun
from training.fine_tune_job_tracker import FineTuneJobTracker
from ops.readiness import ContinuousTrainingReadinessAuditor, ContinuousTrainingReadinessConfig, ContinuousTrainingReadinessResult


class TrainingConsoleConfig(BaseModel):
    """Configuration for the Training Operations Console."""

    output_dir: str = "data/training_console"
    json_only: bool = False
    default_limit: int = 50
    dry_run: bool = False


class ConsoleActionResult(BaseModel):
    """Structured result of any console action."""

    action: str
    success: bool
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    report_json_path: Optional[str] = None
    report_markdown_path: Optional[str] = None


class TrainingConsoleSummary(BaseModel):
    """System-wide summary of pending items and recent jobs."""

    generated_at: str
    pending_review_items: int
    pending_by_type: Dict[str, int] = Field(default_factory=dict)
    recent_training_sources: int
    recent_human_review_items: int
    recent_prompt_versions: int
    recent_canaries: int
    recent_tracking_records: int
    readiness_status: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class TrainingOperationsConsole:
    """Operations console service layer for Dana's continuous training system."""

    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository or Repository()

    def safe_path(self, path: str) -> Path:
        """Resolves path and checks if it's strictly within the repository root."""
        repo_root = Path(__file__).parent.parent.resolve()
        resolved = Path(path).resolve()
        try:
            resolved.relative_to(repo_root)
            return resolved
        except ValueError:
            raise ValueError("Access denied: path is outside the repository directory.")

    def result_from_model(self, action: str, model: Any, message: str) -> ConsoleActionResult:
        """Helper to safely serialize Pydantic/dataclass models to ConsoleActionResult."""
        if isinstance(model, BaseModel):
            data = model.model_dump(mode="json")
        elif dataclasses.is_dataclass(model):
            data = dataclasses.asdict(model)
            # Normalize datetimes
            for k, v in list(data.items()):
                if isinstance(v, datetime):
                    data[k] = v.isoformat()
        elif isinstance(model, dict):
            data = model
        else:
            data = {"result": str(model)}

        report_json_path = data.get("report_json_path")
        report_markdown_path = data.get("report_markdown_path")

        return ConsoleActionResult(
            action=action,
            success=True,
            message=message,
            data=data,
            warnings=data.get("warnings") or [],
            report_json_path=report_json_path,
            report_markdown_path=report_markdown_path,
        )

    async def get_summary(self, limit: int = 50) -> TrainingConsoleSummary:
        """Generates system-wide summary metrics."""
        warnings_list: List[str] = []
        pending_by_type: Dict[str, int] = {}
        pending_count = 0

        # Query pending review items
        try:
            pending_items = await self.repository.list_pending_human_review_items(limit=10000)
            pending_count = len(pending_items)
            for item in pending_items:
                itype = item.get("item_type") or "unknown"
                pending_by_type[itype] = pending_by_type.get(itype, 0) + 1
        except Exception as e:
            warnings_list.append(f"Failed to query pending review items: {e}")

        # Counts for recent items
        recent_sources = 0
        recent_review = 0
        recent_prompt = 0
        recent_canaries = 0
        recent_tracking = 0

        try:
            recent_sources = len(await self.repository.list_recent_training_sources(limit=limit))
        except Exception as e:
            warnings_list.append(f"Failed to query recent training sources: {e}")

        try:
            recent_review = len(await self.repository.list_recent_human_review_items(limit=limit))
        except Exception as e:
            warnings_list.append(f"Failed to query recent human review items: {e}")

        try:
            recent_prompt = len(await self.repository.list_recent_prompt_versions(limit=limit))
        except Exception as e:
            warnings_list.append(f"Failed to query recent prompt versions: {e}")

        try:
            recent_canaries = len(await self.repository.list_recent_deployment_experiments(limit=limit))
        except Exception as e:
            warnings_list.append(f"Failed to query recent deployment experiments: {e}")

        try:
            tracker = FineTuneJobTracker(self.repository)
            recent_tracking = len(await tracker.list_tracking_records(limit=limit))
        except Exception as e:
            warnings_list.append(f"Failed to query recent fine-tune tracking records: {e}")

        # Readiness check (offline dry run)
        readiness_status = "UNKNOWN"
        try:
            auditor = ContinuousTrainingReadinessAuditor()
            # strict=False, output_dir=None to verify quickly without filesystem side effects
            config = ContinuousTrainingReadinessConfig(strict=False, output_dir="")
            readiness_res = auditor.run_all_checks(config)
            readiness_status = "PASS" if readiness_res.passed else "FAIL"
        except Exception as e:
            warnings_list.append(f"Failed to run readiness audit: {e}")

        return TrainingConsoleSummary(
            generated_at=datetime.now(timezone.utc).isoformat(),
            pending_review_items=pending_count,
            pending_by_type=pending_by_type,
            recent_training_sources=recent_sources,
            recent_human_review_items=recent_review,
            recent_prompt_versions=recent_prompt,
            recent_canaries=recent_canaries,
            recent_tracking_records=recent_tracking,
            readiness_status=readiness_status,
            warnings=warnings_list,
        )

    async def list_review_items(
        self, status: str = "pending", item_type: str | None = None, limit: int = 50
    ) -> ConsoleActionResult:
        """List human review items, optionally filtered by status and item type."""
        try:
            if status == "pending":
                service = HumanReviewService(self.repository)
                items = await service.list_pending_review_items(item_type=item_type, limit=limit)
            else:
                filters = {"status": status}
                if item_type:
                    filters["item_type"] = item_type
                items = await self.repository.query_human_review_items(filters)
                items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
                items = items[:limit]

            # Return serializable dicts
            return ConsoleActionResult(
                action="list_review_items",
                success=True,
                message=f"Retrieved {len(items)} review items.",
                data={"items": items},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="list_review_items",
                success=False,
                message="Failed to list review items.",
                error=str(e),
            )

    async def show_review_item(self, item_id: str) -> ConsoleActionResult:
        """Show details of a specific review item."""
        try:
            service = HumanReviewService(self.repository)
            item = await service.get_review_item(item_id)
            return ConsoleActionResult(
                action="show_review_item",
                success=True,
                message="Retrieved review item.",
                data={"item": item},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="show_review_item",
                success=False,
                message="Failed to retrieve review item.",
                error=str(e),
            )

    async def approve_review_item(
        self, item_id: str, reviewer: str, notes: str | None = None
    ) -> ConsoleActionResult:
        """Approve a human review item, creating relevant downstream assets."""
        if not reviewer or not reviewer.strip():
            return ConsoleActionResult(
                action="approve_review_item",
                success=False,
                message="Reviewer is required.",
                error="Reviewer name must not be empty.",
            )
        try:
            service = HumanReviewService(self.repository)
            res = await service.approve_review_item(item_id, reviewer, review_notes=notes)
            return self.result_from_model(
                action="approve_review_item",
                model=res,
                message=res.message,
            )
        except Exception as e:
            return ConsoleActionResult(
                action="approve_review_item",
                success=False,
                message="Failed to approve review item.",
                error=str(e),
            )

    async def reject_review_item(
        self, item_id: str, reviewer: str, notes: str
    ) -> ConsoleActionResult:
        """Reject a human review item. Requires notes."""
        if not reviewer or not reviewer.strip():
            return ConsoleActionResult(
                action="reject_review_item",
                success=False,
                message="Reviewer is required.",
                error="Reviewer name must not be empty.",
            )
        if not notes or not notes.strip():
            return ConsoleActionResult(
                action="reject_review_item",
                success=False,
                message="Notes are required for rejection.",
                error="Review notes are required for rejection.",
            )
        try:
            service = HumanReviewService(self.repository)
            res = await service.reject_review_item(item_id, reviewer, review_notes=notes)
            return self.result_from_model(
                action="reject_review_item",
                model=res,
                message=res.message,
            )
        except Exception as e:
            return ConsoleActionResult(
                action="reject_review_item",
                success=False,
                message="Failed to reject review item.",
                error=str(e),
            )

    async def request_review_changes(
        self, item_id: str, reviewer: str, notes: str
    ) -> ConsoleActionResult:
        """Mark a review item as needing changes. Requires notes."""
        if not reviewer or not reviewer.strip():
            return ConsoleActionResult(
                action="request_review_changes",
                success=False,
                message="Reviewer is required.",
                error="Reviewer name must not be empty.",
            )
        if not notes or not notes.strip():
            return ConsoleActionResult(
                action="request_review_changes",
                success=False,
                message="Notes are required for change requests.",
                error="Review notes are required for changes requested.",
            )
        try:
            service = HumanReviewService(self.repository)
            res = await service.request_changes(item_id, reviewer, review_notes=notes)
            return self.result_from_model(
                action="request_review_changes",
                model=res,
                message=res.message,
            )
        except Exception as e:
            return ConsoleActionResult(
                action="request_review_changes",
                success=False,
                message="Failed to request changes on review item.",
                error=str(e),
            )

    async def run_intake(
        self,
        mode: str,
        path: str | None = None,
        source_type: str | None = None,
        manifest_path: str | None = None,
        daily_qa: bool = False,
        dry_run: bool = False,
        limit: int | None = None,
    ) -> ConsoleActionResult:
        """Run training intake orchestrator workflows."""
        try:
            # Map input parameters to TrainingIntakeConfig
            config = TrainingIntakeConfig(
                mode=mode,
                source_type=source_type,
                input_path=path,
                manifest_path=manifest_path,
                run_daily_qa=daily_qa,
                dry_run=dry_run,
                limit=limit,
            )
            orchestrator = TrainingIntakeOrchestrator(self.repository)
            res = await orchestrator.run(config)
            return self.result_from_model(
                action="run_intake",
                model=res,
                message=f"Intake run finished with status: {res.mode} mode completed.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="run_intake",
                success=False,
                message="Intake execution failed.",
                error=str(e),
            )

    async def import_youtube(
        self,
        file: str | None = None,
        content: str | None = None,
        manifest: str | None = None,
        title: str | None = None,
        source_url: str | None = None,
        run_intake: bool = False,
        dry_run: bool = False,
    ) -> ConsoleActionResult:
        """Import YouTube transcript files or manifests offline."""
        try:
            config = YouTubeTranscriptImportConfig(
                transcript_file=file,
                transcript_text=content,
                manifest_path=manifest,
                title=title,
                source_url=source_url,
                run_intake_after_import=run_intake,
                dry_run=dry_run,
            )
            importer = YouTubeTranscriptImporter(self.repository)
            res = await importer.import_transcripts(config)
            return self.result_from_model(
                action="import_youtube",
                model=res,
                message="YouTube import run completed.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="import_youtube",
                success=False,
                message="YouTube import failed.",
                error=str(e),
            )

    async def run_scheduler_once(
        self, daily_qa: bool = True, dry_run: bool = False, limit: int | None = None
    ) -> ConsoleActionResult:
        """Run scheduler loop exactly once (bounded execution)."""
        try:
            config = TrainingIntakeScheduleConfig(
                mode="once",
                run_daily_qa=daily_qa,
                dry_run=dry_run,
                limit=limit,
                max_runs=1,
            )
            scheduler = TrainingIntakeScheduler(self.repository)
            res = await scheduler.run(config)
            return self.result_from_model(
                action="run_scheduler_once",
                model=res,
                message="Scheduler single-run iteration completed.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="run_scheduler_once",
                success=False,
                message="Scheduler single-run execution failed.",
                error=str(e),
            )

    def run_readiness(self, strict: bool = True, fail_on_medium: bool = False) -> ConsoleActionResult:
        """Run continuous training readiness audit scans."""
        try:
            config = ContinuousTrainingReadinessConfig(
                strict=strict,
                fail_on_medium=fail_on_medium,
                output_dir="data/ops_readiness",
            )
            auditor = ContinuousTrainingReadinessAuditor()
            res = auditor.run_all_checks(config)
            return self.result_from_model(
                action="run_readiness",
                model=res,
                message="Readiness audit completed.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="run_readiness",
                success=False,
                message="Readiness audit failed.",
                error=str(e),
            )

    def list_reports(self, report_type: str | None = None, limit: int = 50) -> ConsoleActionResult:
        """List generated operator reports from data subdirectories securely."""
        try:
            repo_root = Path(__file__).parent.parent.resolve()
            
            # Map of allowed report directories relative to repository root
            report_dirs = {
                "qa": "data/reports",
                "intake": "data/intake_reports",
                "prompt": "data/prompt_versions",
                "canary": "data/canary",
                "fine_tune": "data/fine_tune_job_tracking",
                "readiness": "data/ops_readiness",
            }

            matched_files = []

            for rtype, rpath in report_dirs.items():
                if report_type and report_type != rtype:
                    continue

                full_path = repo_root / rpath
                if not full_path.exists() or not full_path.is_dir():
                    continue

                for filepath in full_path.glob("**/*"):
                    if not filepath.is_file():
                        continue
                    if filepath.suffix not in (".json", ".md", ".diff"):
                        continue

                    # Safe path resolve check
                    safe_fp = self.safe_path(str(filepath))

                    mtime = filepath.stat().st_mtime
                    matched_files.append({
                        "path": str(safe_fp.relative_to(repo_root)).replace("\\", "/"),
                        "name": filepath.name,
                        "type": rtype,
                        "size_bytes": filepath.stat().st_size,
                        "modified_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                    })

            # Sort by modified time descending
            matched_files.sort(key=lambda x: x["modified_at"], reverse=True)
            matched_files = matched_files[:limit]

            return ConsoleActionResult(
                action="list_reports",
                success=True,
                message=f"Discovered {len(matched_files)} reports.",
                data={"reports": matched_files},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="list_reports",
                success=False,
                message="Failed to discover reports.",
                error=str(e),
            )

    def read_report(self, path: str) -> ConsoleActionResult:
        """Read text content of a report after verifying security paths."""
        try:
            safe_filepath = self.safe_path(path)
            if not safe_filepath.exists() or not safe_filepath.is_file():
                raise FileNotFoundError(f"Report file not found: {path}")

            text = safe_filepath.read_text(encoding="utf-8")
            return ConsoleActionResult(
                action="read_report",
                success=True,
                message="Report read successfully.",
                data={"content": text},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="read_report",
                success=False,
                message="Failed to read report.",
                error=str(e),
            )
