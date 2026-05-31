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
                "intake": "data/intake_reports",
                "qa": "data/reports",
                "eval": "data/evals",
                "replay": "data/evals",
                "simulation": "data/simulations",
                "prompt_patch": "data/prompt_patches",
                "canary": "data/canary",
                "fine_tune": "data/fine_tune_job_tracking",
                "readiness": "data/ops_readiness",
                "scheduler": "data/intake_reports",
                "web_console": "data/training_console",
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

    # =========================================================================
    # QA / Evaluation Methods
    # =========================================================================

    async def run_daily_qa(
        self,
        date: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        dry_run: bool = False,
        limit: int | None = None,
        output_dir: str | None = None,
    ) -> ConsoleActionResult:
        """Processes Daily QA Mining over a date or date range."""
        try:
            from training.daily_qa_miner import DailyQaMiner
            miner = DailyQaMiner(repository=self.repository)

            if output_dir:
                original_write_report = miner.write_daily_report
                def custom_write_report(result, analysis_result):
                    return original_write_report(result, analysis_result, output_dir=output_dir)
                miner.write_daily_report = custom_write_report

            if date:
                res = await miner.mine_date(date, dry_run=dry_run)
            elif date_from and date_to:
                res = await miner.mine_range(date_from, date_to, dry_run=dry_run)
            else:
                raise ValueError("Either 'date' or both 'date_from' and 'date_to' must be provided.")

            return self.result_from_model(
                action="run_daily_qa",
                model=res,
                message="Daily QA mining finished successfully.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="run_daily_qa",
                success=False,
                message="Daily QA mining failed.",
                error=str(e),
            )

    async def run_eval_cases(
        self,
        case_id: str | None = None,
        stage: str | None = None,
        objection: str | None = None,
        limit: int | None = None,
        output_dir: str | None = None,
    ) -> ConsoleActionResult:
        """Executes regression test cases using StaticResponseProvider."""
        try:
            from evals.case_runner import EvalCaseRunner, EvalCaseRunConfig, StaticResponseProvider

            case_ids = [case_id] if case_id else None
            config = EvalCaseRunConfig(
                case_ids=case_ids,
                stage=stage,
                limit=limit,
                response_mode="static",
                output_dir=output_dir or "data/evals",
            )

            # Query approved cases first to apply objection filter if requested
            if objection:
                all_cases = await self.repository.list_recent_eval_cases(limit=1000)
                filtered = []
                for c in all_cases:
                    # Treat stored cases as approved by default unless explicitly marked otherwise
                    is_approved = True
                    if "status" in c and c["status"] not in ("approved", "active"):
                        is_approved = False
                    elif "approved" in c and not c["approved"]:
                        is_approved = False
                    elif c.get("metadata", {}).get("approved") is False:
                        is_approved = False

                    if not is_approved:
                        continue

                    # Filter by parameters
                    if case_ids and c.get("id") not in case_ids:
                        continue
                    if stage and c.get("stage") != stage:
                        continue

                    # Check expected_behavior or labels for objection string
                    eb = (c.get("expected_behavior") or "").lower()
                    if objection.lower() in eb or c.get("objection_type") == objection or c.get("metadata", {}).get("objection_type") == objection:
                        filtered.append(c.get("id"))
                
                if not filtered:
                    return ConsoleActionResult(
                        action="run_eval_cases",
                        success=True,
                        message="No approved eval cases matching objection filter.",
                        data={"total_cases": 0, "results": []},
                    )
                config.case_ids = filtered

            # Create default static provider
            # For testing, we can provide a default responses dictionary if any fixtures match
            provider = StaticResponseProvider(fallback_response="I understand, let's get you to a specialist who can help.")
            runner = EvalCaseRunner(repository=self.repository, response_provider=provider)
            res = await runner.run_approved_cases(config)

            return self.result_from_model(
                action="run_eval_cases",
                model=res,
                message="Eval cases run finished.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="run_eval_cases",
                success=False,
                message="Eval cases run failed.",
                error=str(e),
            )

    async def run_transcript_replay(
        self,
        fixture: str | None = None,
        fixture_dir: str | None = None,
        mode: str = "static",
        fail_fast: bool = False,
        output_dir: str | None = None,
    ) -> ConsoleActionResult:
        """Runs multi-turn replays via StaticTranscriptResponseProvider."""
        try:
            from evals.transcript_replay import (
                TranscriptReplayRunner,
                StaticTranscriptResponseProvider,
                RuntimeTranscriptResponseProvider,
            )

            # Determine target path
            if fixture:
                target_path = Path(fixture)
            else:
                target_path = Path(fixture_dir or "evals/fixtures/transcripts")

            if mode == "runtime":
                # Ensure runtime provider has keys but don't call actual endpoints here
                import os
                if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("TELNYX_API_KEY"):
                    raise ValueError("Missing environment API keys for OpenAI/Telnyx runtime execution.")
                provider = RuntimeTranscriptResponseProvider()
            else:
                provider = StaticTranscriptResponseProvider()

            runner = TranscriptReplayRunner(response_provider=provider)
            fixtures = runner.load_fixtures(target_path)
            
            res = await runner.replay_fixtures(
                fixtures,
                output_dir=output_dir or "data/evals",
                fail_fast=fail_fast,
            )

            return self.result_from_model(
                action="run_transcript_replay",
                model=res,
                message="Transcript replay test run completed.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="run_transcript_replay",
                success=False,
                message="Transcript replay test run failed.",
                error=str(e),
            )

    async def run_prospect_simulations(
        self,
        persona: str | None = None,
        run_all: bool = False,
        output_dir: str | None = None,
    ) -> ConsoleActionResult:
        """Simulates personas with StaticDanaResponseProvider."""
        try:
            from simulations.prospect_simulator import (
                ProspectSimulator,
                SimulationRunner,
                StaticDanaResponseProvider,
                SimulationRunResult,
                SimulationResult,
            )
            import uuid

            simulator = ProspectSimulator()
            dana_provider = StaticDanaResponseProvider()
            runner = SimulationRunner(simulator=simulator, dana_provider=dana_provider)

            # Resolve personas
            personas_to_run = []
            if run_all:
                personas_to_run = [p.id for p in simulator.get_default_personas()]
            elif persona:
                personas_to_run = [persona]
            else:
                raise ValueError("Must specify either 'persona' or set 'run_all' to True.")

            started_at = datetime.now(timezone.utc).isoformat()
            results = []
            passed_scenarios = 0
            failed_scenarios = 0
            total_score = 0.0

            out_dir = output_dir or "data/simulations"

            for pid in personas_to_run:
                res = await runner.run_persona(pid, output_dir=out_dir)
                results.append(res)
                if res.passed:
                    passed_scenarios += 1
                else:
                    failed_scenarios += 1
                total_score += res.score

            finished_at = datetime.now(timezone.utc).isoformat()
            total_runs = len(results)
            pass_rate = passed_scenarios / total_runs if total_runs > 0 else 0.0
            avg_score = total_score / total_runs if total_runs > 0 else 0.0

            run_id = str(uuid.uuid4())
            obj = SimulationRunResult(
                run_id=run_id,
                started_at=started_at,
                finished_at=finished_at,
                total_scenarios=total_runs,
                passed_scenarios=passed_scenarios,
                failed_scenarios=failed_scenarios,
                pass_rate=pass_rate,
                average_score=avg_score,
                results=results,
                warnings=[],
            )

            runner.write_run_report(obj, out_dir)
            
            # Convert to ConsoleActionResult serializable dict
            data = json_serializable(obj)
            data["report_json_path"] = os.path.join(out_dir, f"simulation_run_{run_id}.json")
            data["report_markdown_path"] = os.path.join(out_dir, f"simulation_run_{run_id}.md")

            return ConsoleActionResult(
                action="run_prospect_simulations",
                success=True,
                message="Prospect simulations finished.",
                data=data,
                report_json_path=data["report_json_path"],
                report_markdown_path=data["report_markdown_path"],
            )
        except Exception as e:
            return ConsoleActionResult(
                action="run_prospect_simulations",
                success=False,
                message="Prospect simulations failed.",
                error=str(e),
            )

    # =========================================================================
    # Prompt Methods
    # =========================================================================

    async def list_prompt_versions(self, limit: int = 50) -> ConsoleActionResult:
        """List recent prompt versions."""
        try:
            from prompts.versioning import PromptVersionManager
            manager = PromptVersionManager()
            versions = await manager.list_prompt_versions(limit=limit)
            return ConsoleActionResult(
                action="list_prompt_versions",
                success=True,
                message=f"Retrieved {len(versions)} prompt versions.",
                data={"versions": json_serializable(versions)},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="list_prompt_versions",
                success=False,
                message="Failed to list prompt versions.",
                error=str(e),
            )

    async def generate_prompt_patches(
        self,
        dry_run: bool = False,
        limit: int | None = None,
        output_dir: str | None = None,
    ) -> ConsoleActionResult:
        """Generates safe prompt patch candidates for human review."""
        try:
            from prompts.patch_generator import PromptPatchGenerator
            generator = PromptPatchGenerator(self.repository)
            res = await generator.generate_for_prompt(
                prompt_name="final_expense_alex",
                prompt_path="prompts/final_expense_alex.md",
                limit=limit or 500,
                save_review_items=not dry_run,
                output_dir=output_dir or "data/prompt_patches",
            )
            return self.result_from_model(
                action="generate_prompt_patches",
                model=res,
                message="Prompt patch generation completed.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="generate_prompt_patches",
                success=False,
                message="Failed to generate prompt patches.",
                error=str(e),
            )

    async def preview_prompt_patches(
        self,
        patch_id: str | None = None,
        approved_only: bool = True,
        create_candidate_version: bool = False,
        skip_gates: bool = False,
        output_dir: str | None = None,
    ) -> ConsoleActionResult:
        """Preview prompt patches and run safety and regression gates."""
        try:
            from prompts.patch_preview import PromptPatchPreviewer
            previewer = PromptPatchPreviewer(repository=self.repository)
            patch_ids = [patch_id] if patch_id else None

            # Note: build_preview filters/validates approvals internally when patch_ids is loaded
            res = await previewer.build_preview(
                prompt_name="final_expense_alex",
                prompt_path="prompts/final_expense_alex.md",
                patch_ids=patch_ids,
                output_dir=output_dir or "data/prompt_patches/previews",
                run_gates=not skip_gates,
                create_candidate_version=create_candidate_version,
            )
            return self.result_from_model(
                action="preview_prompt_patches",
                model=res,
                message="Prompt patch preview completed.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="preview_prompt_patches",
                success=False,
                message="Failed to preview prompt patches.",
                error=str(e),
            )

    # =========================================================================
    # Canary Rollout Methods
    # =========================================================================

    async def list_canaries(self, limit: int = 50) -> ConsoleActionResult:
        """List canary deployment experiments."""
        try:
            from deployment.canary import CanaryManager
            manager = CanaryManager(repository=self.repository)
            canaries = await manager.list_canaries(limit=limit)
            return ConsoleActionResult(
                action="list_canaries",
                success=True,
                message=f"Retrieved {len(canaries)} canary experiments.",
                data={"canaries": canaries},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="list_canaries",
                success=False,
                message="Failed to list canaries.",
                error=str(e),
            )

    async def show_canary(self, experiment_id: str) -> ConsoleActionResult:
        """Show details of a specific canary experiment."""
        try:
            from deployment.canary import CanaryManager
            manager = CanaryManager(repository=self.repository)
            canary = await manager.get_canary(experiment_id)
            return ConsoleActionResult(
                action="show_canary",
                success=True,
                message="Retrieved canary experiment.",
                data={"canary": canary},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="show_canary",
                success=False,
                message="Failed to show canary.",
                error=str(e),
            )

    async def check_canary_candidate(self, prompt_version_id: str) -> ConsoleActionResult:
        """Verify candidate prompt version eligibility for canary rollout."""
        try:
            from deployment.canary import CanaryManager
            manager = CanaryManager(repository=self.repository)
            res = await manager.check_candidate_eligibility(prompt_version_id)
            return self.result_from_model(
                action="check_canary_candidate",
                model=res,
                message="Canary candidate eligibility checked.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="check_canary_candidate",
                success=False,
                message="Failed to check canary candidate.",
                error=str(e),
            )

    async def create_canary_plan(
        self,
        prompt_version_id: str,
        traffic_percent: float,
        operator: str,
        notes: str | None = None,
    ) -> ConsoleActionResult:
        """Create planned canary rollout plan."""
        if not operator or not operator.strip():
            return ConsoleActionResult(
                action="create_canary_plan",
                success=False,
                message="Operator is required.",
                error="Operator name must not be empty.",
            )
        try:
            from deployment.canary import CanaryManager
            manager = CanaryManager(repository=self.repository)
            experiment_name = f"canary_{prompt_version_id[:8]}"
            plan = await manager.create_canary_plan(
                candidate_prompt_version_id=prompt_version_id,
                experiment_name=experiment_name,
                created_by=operator,
                traffic_percentage=traffic_percent,
                max_traffic_percentage=10.0,
            )
            return self.result_from_model(
                action="create_canary_plan",
                model=plan,
                message="Canary rollout plan created.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="create_canary_plan",
                success=False,
                message="Failed to create canary plan.",
                error=str(e),
            )

    async def approve_canary(self, experiment_id: str, operator: str, notes: str) -> ConsoleActionResult:
        """Approve planned canary experiment."""
        if not operator or not operator.strip():
            return ConsoleActionResult(
                action="approve_canary",
                success=False,
                message="Operator is required.",
                error="Operator name must not be empty.",
            )
        try:
            from deployment.canary import CanaryManager
            manager = CanaryManager(repository=self.repository)
            res = await manager.approve_canary(
                experiment_id=experiment_id,
                approved_by=operator,
                approval_notes=notes,
            )
            return self.result_from_model(
                action="approve_canary",
                model=res,
                message="Canary experiment approved.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="approve_canary",
                success=False,
                message="Failed to approve canary.",
                error=str(e),
            )

    async def start_canary(self, experiment_id: str, operator: str, notes: str) -> ConsoleActionResult:
        """Start approved canary experiment."""
        if not operator or not operator.strip():
            return ConsoleActionResult(
                action="start_canary",
                success=False,
                message="Operator is required.",
                error="Operator name must not be empty.",
            )
        try:
            from deployment.canary import CanaryManager
            manager = CanaryManager(repository=self.repository)
            res = await manager.start_canary(
                experiment_id=experiment_id,
                started_by=operator,
            )
            return self.result_from_model(
                action="start_canary",
                model=res,
                message="Canary experiment started.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="start_canary",
                success=False,
                message="Failed to start canary.",
                error=str(e),
            )

    async def pause_canary(self, experiment_id: str, operator: str, notes: str) -> ConsoleActionResult:
        """Pause running canary experiment."""
        if not operator or not operator.strip():
            return ConsoleActionResult(
                action="pause_canary",
                success=False,
                message="Operator is required.",
                error="Operator name must not be empty.",
            )
        try:
            from deployment.canary import CanaryManager
            manager = CanaryManager(repository=self.repository)
            res = await manager.pause_canary(
                experiment_id=experiment_id,
                paused_by=operator,
                reason=notes,
            )
            return self.result_from_model(
                action="pause_canary",
                model=res,
                message="Canary experiment paused.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="pause_canary",
                success=False,
                message="Failed to pause canary.",
                error=str(e),
            )

    async def rollback_canary(self, experiment_id: str, operator: str, reason: str) -> ConsoleActionResult:
        """Roll back running or paused canary experiment. Requires a reason."""
        if not operator or not operator.strip():
            return ConsoleActionResult(
                action="rollback_canary",
                success=False,
                message="Operator is required.",
                error="Operator name must not be empty.",
            )
        if not reason or not reason.strip():
            return ConsoleActionResult(
                action="rollback_canary",
                success=False,
                message="Reason is required for rollback.",
                error="Rollback reason must not be empty.",
            )
        try:
            from deployment.canary import CanaryManager
            manager = CanaryManager(repository=self.repository)
            res = await manager.rollback_canary(
                experiment_id=experiment_id,
                rolled_back_by=operator,
                reason=reason,
            )
            return self.result_from_model(
                action="rollback_canary",
                model=res,
                message="Canary experiment rolled back.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="rollback_canary",
                success=False,
                message="Failed to rollback canary.",
                error=str(e),
            )

    async def complete_canary(self, experiment_id: str, operator: str, notes: str) -> ConsoleActionResult:
        """Complete running canary experiment."""
        if not operator or not operator.strip():
            return ConsoleActionResult(
                action="complete_canary",
                success=False,
                message="Operator is required.",
                error="Operator name must not be empty.",
            )
        try:
            from deployment.canary import CanaryManager
            manager = CanaryManager(repository=self.repository)
            res = await manager.complete_canary(
                experiment_id=experiment_id,
                completed_by=operator,
                reason=notes,
            )
            return self.result_from_model(
                action="complete_canary",
                model=res,
                message="Canary experiment completed.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="complete_canary",
                success=False,
                message="Failed to complete canary.",
                error=str(e),
            )

    async def cancel_canary(self, experiment_id: str, operator: str, reason: str) -> ConsoleActionResult:
        """Cancel canary experiment. Requires a reason."""
        if not operator or not operator.strip():
            return ConsoleActionResult(
                action="cancel_canary",
                success=False,
                message="Operator is required.",
                error="Operator name must not be empty.",
            )
        if not reason or not reason.strip():
            return ConsoleActionResult(
                action="cancel_canary",
                success=False,
                message="Reason is required for cancellation.",
                error="Cancellation reason must not be empty.",
            )
        try:
            from deployment.canary import CanaryManager
            manager = CanaryManager(repository=self.repository)
            res = await manager.cancel_canary(
                experiment_id=experiment_id,
                cancelled_by=operator,
                reason=reason,
            )
            return self.result_from_model(
                action="cancel_canary",
                model=res,
                message="Canary experiment cancelled.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="cancel_canary",
                success=False,
                message="Failed to cancel canary.",
                error=str(e),
            )

    async def monitor_canary(
        self,
        experiment_id: str | None = None,
        output_dir: str | None = None,
    ) -> ConsoleActionResult:
        """Run canary monitoring evaluation reports."""
        try:
            from deployment.monitoring import CanaryMonitor, CanaryMonitorConfig
            
            exp_id = experiment_id
            if not exp_id:
                recent = await self.repository.list_recent_deployment_experiments(limit=50)
                active = [e for e in recent if e.get("status") in ("running", "active")]
                if active:
                    exp_id = active[0].get("id")
                elif recent:
                    exp_id = recent[0].get("id")
                else:
                    return ConsoleActionResult(
                        action="monitor_canary",
                        success=False,
                        message="No canary experiments found to monitor.",
                    )

            monitor = CanaryMonitor(repository=self.repository)
            config = CanaryMonitorConfig(
                experiment_id=exp_id,
                output_dir=output_dir or "data/canary"
            )
            res = await monitor.monitor_experiment(config)
            return self.result_from_model(
                action="monitor_canary",
                model=res,
                message=f"Canary monitoring finished for experiment {exp_id}.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="monitor_canary",
                success=False,
                message="Canary monitoring failed.",
                error=str(e),
            )

    # =========================================================================
    # Fine-Tune Methods
    # =========================================================================

    async def export_fine_tune_dataset(
        self,
        dry_run: bool = False,
        limit: int | None = None,
        stage: str | None = None,
        objection: str | None = None,
        output_dir: str | None = None,
    ) -> ConsoleActionResult:
        """Build fine-tune export package from approved examples."""
        try:
            from training.fine_tune_export import FineTuneExportBuilder, FineTuneExportConfig

            include_stages = [stage] if stage else None
            include_objection_types = [objection] if objection else None

            config = FineTuneExportConfig(
                export_name="fine_tune_web_export",
                format="openai_chat_jsonl",
                output_dir=output_dir or "data/fine_tune_exports",
                train_ratio=0.90,
                limit=limit,
                min_examples=5,
                require_fine_tune_eligible=True,
                include_stages=include_stages,
                include_objection_types=include_objection_types,
                dry_run=dry_run,
            )
            builder = FineTuneExportBuilder(repository=self.repository)
            res = await builder.build_export(config)
            return self.result_from_model(
                action="export_fine_tune_dataset",
                model=res,
                message="Fine-tune dataset export completed.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="export_fine_tune_dataset",
                success=False,
                message="Fine-tune dataset export failed.",
                error=str(e),
            )

    async def gate_fine_tune_dataset(
        self,
        dataset_path: str,
        strict: bool = True,
        output_dir: str | None = None,
    ) -> ConsoleActionResult:
        """Run fine-tune dataset gate scans on export files."""
        try:
            from training.fine_tune_gate import FineTuneDatasetGate, FineTuneDatasetGateConfig

            p = Path(dataset_path)
            manifest = None
            train = None
            validation = None

            if p.is_dir():
                manifest_json = p / "manifest.json"
                if manifest_json.exists():
                    manifest = str(manifest_json)
                else:
                    jsonls = list(p.glob("*.jsonl"))
                    train_files = [str(f) for f in jsonls if "train" in f.name]
                    val_files = [str(f) for f in jsonls if "val" in f.name or "validation" in f.name]
                    if train_files:
                        train = train_files[0]
                    if val_files:
                        validation = val_files[0]
            elif p.suffix == ".json":
                manifest = str(p)
            elif p.suffix == ".jsonl":
                train = str(p)
                val_candidate = p.parent / p.name.replace("train", "val").replace("train", "validation")
                if val_candidate.exists():
                    validation = str(val_candidate)

            if not manifest and not train:
                return ConsoleActionResult(
                    action="gate_fine_tune_dataset",
                    success=False,
                    message="Failed to resolve dataset files from dataset_path.",
                    error=f"dataset_path '{dataset_path}' must be a directory, manifest JSON, or JSONL file.",
                )

            config = FineTuneDatasetGateConfig(
                manifest_path=manifest,
                train_path=train,
                validation_path=validation,
                output_dir=output_dir or "data/fine_tune_approvals",
                fail_on_medium_warnings=strict,
            )

            gate = FineTuneDatasetGate(repository=self.repository)
            res = await gate.run_gate(config)
            return self.result_from_model(
                action="gate_fine_tune_dataset",
                model=res,
                message="Fine-tune dataset gate run finished.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="gate_fine_tune_dataset",
                success=False,
                message="Fine-tune dataset gate failed.",
                error=str(e),
            )

    async def prepare_fine_tune_job_request(
        self,
        dataset_path: str,
        gate_report_path: str | None = None,
        provider: str = "openai",
        output_dir: str | None = None,
        dry_run: bool = False,
    ) -> ConsoleActionResult:
        """Prepare fine-tuning job request package configuration."""
        try:
            from training.fine_tune_job_request import FineTuneJobRequestBuilder, FineTuneJobRequestConfig

            p = Path(dataset_path)
            manifest = None
            train = None
            validation = None

            if p.is_dir():
                manifest_json = p / "manifest.json"
                if manifest_json.exists():
                    manifest = str(manifest_json)
                else:
                    jsonls = list(p.glob("*.jsonl"))
                    train_files = [str(f) for f in jsonls if "train" in f.name]
                    val_files = [str(f) for f in jsonls if "val" in f.name or "validation" in f.name]
                    if train_files:
                        train = train_files[0]
                    if val_files:
                        validation = val_files[0]
            elif p.suffix == ".json":
                manifest = str(p)
            elif p.suffix == ".jsonl":
                train = str(p)
                val_candidate = p.parent / p.name.replace("train", "val").replace("train", "validation")
                if val_candidate.exists():
                    validation = str(val_candidate)

            require_checks = gate_report_path is not None

            config = FineTuneJobRequestConfig(
                approval_package_path=gate_report_path,
                manifest_path=manifest,
                train_path=train,
                validation_path=validation,
                provider=provider,
                output_dir=output_dir or "data/fine_tune_job_requests",
                require_human_approval=require_checks,
                require_gate_passed=require_checks,
                require_hash_match=require_checks,
                dry_run=dry_run,
            )
            builder = FineTuneJobRequestBuilder(repository=self.repository)
            res = await builder.build_request_package(config)
            return self.result_from_model(
                action="prepare_fine_tune_job_request",
                model=res,
                message="Fine-tune job request preparation finished.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="prepare_fine_tune_job_request",
                success=False,
                message="Fine-tune job request preparation failed.",
                error=str(e),
            )

    async def track_fine_tune_job(
        self,
        job_request_id: str,
        status: str,
        operator: str,
        notes: str | None = None,
        provider_job_id: str | None = None,
    ) -> ConsoleActionResult:
        """Track manual fine-tuning job status transitions."""
        if not operator or not operator.strip():
            return ConsoleActionResult(
                action="track_fine_tune_job",
                success=False,
                message="Operator is required.",
                error="Operator name must not be empty.",
            )
        try:
            from training.fine_tune_job_tracker import FineTuneJobTracker, FineTuneJobTrackerConfig
            tracker = FineTuneJobTracker(repository=self.repository)

            record = None
            try:
                record = await tracker.get_tracking_record(job_request_id)
            except Exception:
                pass

            if record:
                res = await tracker.update_manual_status(
                    tracking_id=job_request_id,
                    new_status=status,
                    actor=operator,
                    reason=notes or "Status updated via console.",
                )
            else:
                config = FineTuneJobTrackerConfig(
                    job_request_review_item_id=job_request_id,
                    actor=operator,
                    reason=notes or "Status transition via console.",
                    notes=notes,
                )
                if provider_job_id:
                    config.provider_job_id = provider_job_id
                    config.job_start_review_item_id = job_request_id
                    res = await tracker.record_manual_job_start(config)
                else:
                    res = await tracker.create_start_approval_request(config)

            return self.result_from_model(
                action="track_fine_tune_job",
                model=res,
                message="Fine-tune job tracking updated.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="track_fine_tune_job",
                success=False,
                message="Failed to update fine-tune job tracking.",
                error=str(e),
            )

    async def list_fine_tune_tracking(self, limit: int = 50) -> ConsoleActionResult:
        """List fine-tuning job tracking records."""
        try:
            from training.fine_tune_job_tracker import FineTuneJobTracker
            tracker = FineTuneJobTracker(repository=self.repository)
            records = await tracker.list_tracking_records(limit=limit)
            return ConsoleActionResult(
                action="list_fine_tune_tracking",
                success=True,
                message=f"Retrieved {len(records)} fine-tune tracking records.",
                data={"records": records},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="list_fine_tune_tracking",
                success=False,
                message="Failed to list fine-tune tracking records.",
                error=str(e),
            )

    # =========================================================================
    # Post-Call Export Method
    # =========================================================================

    async def export_completed_call_payload(
        self,
        payload: dict,
        enabled: bool = True,
        run_intake: bool = False,
        dry_run: bool = False,
        output_dir: str | None = None,
    ) -> ConsoleActionResult:
        """Export completed call payload for training ingestion."""
        try:
            from training.post_call_exporter import PostCallExporter, PostCallExportConfig
            config = PostCallExportConfig(
                enabled=enabled,
                output_dir=output_dir or "data/imports/post_call_payloads",
                run_intake_after_export=run_intake,
                intake_sync=True,
                dry_run=dry_run,
                fail_silently=False,
            )
            exporter = PostCallExporter()
            res = await exporter.export_completed_call(payload, config)
            return self.result_from_model(
                action="export_completed_call_payload",
                model=res,
                message="Post-call payload export completed.",
            )
        except Exception as e:
            return ConsoleActionResult(
                action="export_completed_call_payload",
                success=False,
                message="Post-call payload export failed.",
                error=str(e),
            )

    # =========================================================================
    # Telephony Operations Methods
    # =========================================================================

    async def create_telephony_provider_config(self, **kwargs: Any) -> ConsoleActionResult:
        """Create a new TelephonyProviderConfig."""
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            config_id = await service.create_provider_config(**kwargs)
            return ConsoleActionResult(
                action="create_telephony_provider_config",
                success=True,
                message="Provider config created successfully.",
                data={"provider_config_id": config_id},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="create_telephony_provider_config",
                success=False,
                message="Failed to create provider config.",
                error=str(e),
            )

    async def list_telephony_provider_configs(self, limit: int = 50) -> ConsoleActionResult:
        """List provider configs."""
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            configs = await service.list_provider_configs(limit=limit)
            return ConsoleActionResult(
                action="list_telephony_provider_configs",
                success=True,
                message=f"Retrieved {len(configs)} provider configs.",
                data={"configs": configs},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="list_telephony_provider_configs",
                success=False,
                message="Failed to list provider configs.",
                error=str(e),
            )

    async def show_telephony_provider_config(self, provider_config_id: str) -> ConsoleActionResult:
        """Show detail for a provider config."""
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            config = await service.get_provider_config(provider_config_id)
            if not config:
                return ConsoleActionResult(
                    action="show_telephony_provider_config",
                    success=False,
                    message=f"Provider config {provider_config_id} not found.",
                    error="NOT_FOUND",
                )
            return ConsoleActionResult(
                action="show_telephony_provider_config",
                success=True,
                message="Provider config retrieved.",
                data={"config": config},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="show_telephony_provider_config",
                success=False,
                message="Failed to retrieve provider config.",
                error=str(e),
            )

    async def create_telephony_campaign(self, **kwargs: Any) -> ConsoleActionResult:
        """Create a outbound campaign."""
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            campaign_id = await service.create_campaign(**kwargs)
            return ConsoleActionResult(
                action="create_telephony_campaign",
                success=True,
                message="Campaign created successfully.",
                data={"campaign_id": campaign_id},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="create_telephony_campaign",
                success=False,
                message="Failed to create campaign.",
                error=str(e),
            )

    async def list_telephony_campaigns(self, status: str | None = None, limit: int = 50) -> ConsoleActionResult:
        """List campaigns."""
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            campaigns = await service.list_campaigns(status=status, limit=limit)
            return ConsoleActionResult(
                action="list_telephony_campaigns",
                success=True,
                message=f"Retrieved {len(campaigns)} campaigns.",
                data={"campaigns": campaigns},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="list_telephony_campaigns",
                success=False,
                message="Failed to list campaigns.",
                error=str(e),
            )

    async def show_telephony_campaign(self, campaign_id: str) -> ConsoleActionResult:
        """Show detail for a campaign."""
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            campaign = await service.get_campaign(campaign_id)
            if not campaign:
                return ConsoleActionResult(
                    action="show_telephony_campaign",
                    success=False,
                    message=f"Campaign {campaign_id} not found.",
                    error="NOT_FOUND",
                )
            return ConsoleActionResult(
                action="show_telephony_campaign",
                success=True,
                message="Campaign retrieved.",
                data={"campaign": campaign},
            )
        except Exception as e:
            return ConsoleActionResult(
                action="show_telephony_campaign",
                success=False,
                message="Failed to retrieve campaign.",
                error=str(e),
            )

    async def mark_campaign_ready(self, campaign_id: str, operator: str, reason: str | None = None) -> ConsoleActionResult:
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            res = await service.mark_ready(campaign_id, operator, reason)
            return self.result_from_model("mark_campaign_ready", res, res.message)
        except Exception as e:
            return ConsoleActionResult(action="mark_campaign_ready", success=False, message="Failed to mark campaign ready.", error=str(e))

    async def start_telephony_campaign(self, campaign_id: str, operator: str, reason: str | None = None) -> ConsoleActionResult:
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            res = await service.start_campaign(campaign_id, operator, reason)
            return self.result_from_model("start_telephony_campaign", res, res.message)
        except Exception as e:
            return ConsoleActionResult(action="start_telephony_campaign", success=False, message="Failed to start campaign.", error=str(e))

    async def pause_telephony_campaign(self, campaign_id: str, operator: str, reason: str | None = None) -> ConsoleActionResult:
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            res = await service.pause_campaign(campaign_id, operator, reason)
            return self.result_from_model("pause_telephony_campaign", res, res.message)
        except Exception as e:
            return ConsoleActionResult(action="pause_telephony_campaign", success=False, message="Failed to pause campaign.", error=str(e))

    async def resume_telephony_campaign(self, campaign_id: str, operator: str, reason: str | None = None) -> ConsoleActionResult:
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            res = await service.resume_campaign(campaign_id, operator, reason)
            return self.result_from_model("resume_telephony_campaign", res, res.message)
        except Exception as e:
            return ConsoleActionResult(action="resume_telephony_campaign", success=False, message="Failed to resume campaign.", error=str(e))

    async def stop_telephony_campaign(self, campaign_id: str, operator: str, reason: str | None = None) -> ConsoleActionResult:
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            res = await service.stop_campaign(campaign_id, operator, reason)
            return self.result_from_model("stop_telephony_campaign", res, res.message)
        except Exception as e:
            return ConsoleActionResult(action="stop_telephony_campaign", success=False, message="Failed to stop campaign.", error=str(e))

    async def complete_telephony_campaign(self, campaign_id: str, operator: str, reason: str | None = None) -> ConsoleActionResult:
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            res = await service.complete_campaign(campaign_id, operator, reason)
            return self.result_from_model("complete_telephony_campaign", res, res.message)
        except Exception as e:
            return ConsoleActionResult(action="complete_telephony_campaign", success=False, message="Failed to complete campaign.", error=str(e))

    async def get_telephony_campaign_summary(self, campaign_id: str) -> ConsoleActionResult:
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            res = await service.get_campaign_summary(campaign_id)
            return self.result_from_model("get_telephony_campaign_summary", res, "Campaign summary calculated.")
        except Exception as e:
            return ConsoleActionResult(action="get_telephony_campaign_summary", success=False, message="Failed to calculate campaign summary.", error=str(e))

    async def import_campaign_leads(self, campaign_id: str, path: str) -> ConsoleActionResult:
        try:
            from telephony.lead_importer import CampaignLeadImporter
            importer = CampaignLeadImporter(repository=self.repository)
            # Resolve safe path
            resolved_path = self.safe_path(path)
            res = await importer.import_file(campaign_id, resolved_path)
            return self.result_from_model("import_campaign_leads", res, "Leads import completed.")
        except Exception as e:
            return ConsoleActionResult(action="import_campaign_leads", success=False, message="Failed to import campaign leads.", error=str(e))

    async def list_campaign_leads(self, campaign_id: str, limit: int = 50) -> ConsoleActionResult:
        try:
            leads = await self.repository.query_campaign_leads({"campaign_id": campaign_id})
            return ConsoleActionResult(
                action="list_campaign_leads",
                success=True,
                message=f"Retrieved {len(leads)} leads.",
                data={"leads": leads[:limit]},
            )
        except Exception as e:
            return ConsoleActionResult(action="list_campaign_leads", success=False, message="Failed to list campaign leads.", error=str(e))

    async def run_dialer_once(
        self,
        campaign_id: str,
        live_mode: bool = False,
        dry_run: bool = True,
        max_calls: int | None = None,
        operator: str | None = None,
        force: bool = False,
    ) -> ConsoleActionResult:
        try:
            from telephony.dialer_queue import DialerQueue, DialerTickConfig
            from telephony.livekit_adapter import LiveKitOutboundAdapter
            adapter = LiveKitOutboundAdapter()
            if live_mode and not adapter.live_mode_enabled():
                return ConsoleActionResult(
                    action="run_dialer_once",
                    success=False,
                    message="Live mode is not enabled. Ensure TELEPHONY_LIVE_MODE=true and DANA_ENABLE_OUTBOUND_DIALER=true are set.",
                    error="Live mode is not enabled. Ensure TELEPHONY_LIVE_MODE=true and DANA_ENABLE_OUTBOUND_DIALER=true are set."
                )
            dialer = DialerQueue(repository=self.repository, adapter=adapter)
            config = DialerTickConfig(
                campaign_id=campaign_id,
                live_mode=live_mode,
                dry_run=dry_run,
                max_calls=max_calls,
                operator=operator or "system",
                force=force,
            )
            res = await dialer.run_tick(config)
            return self.result_from_model("run_dialer_once", res, "Dialer tick completed.")
        except Exception as e:
            return ConsoleActionResult(action="run_dialer_once", success=False, message="Failed to run dialer tick.", error=str(e))

    async def list_live_telephony_calls(self, campaign_id: str | None = None, limit: int = 100) -> ConsoleActionResult:
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            calls = await service.list_live_calls(campaign_id=campaign_id, limit=limit)
            return ConsoleActionResult(
                action="list_live_telephony_calls",
                success=True,
                message=f"Retrieved {len(calls)} live calls.",
                data={"calls": calls},
            )
        except Exception as e:
            return ConsoleActionResult(action="list_live_telephony_calls", success=False, message="Failed to list live calls.", error=str(e))

    async def list_call_attempts(
        self, campaign_id: str | None = None, lead_id: str | None = None, limit: int = 100
    ) -> ConsoleActionResult:
        try:
            from telephony.campaign_service import TelephonyCampaignService
            service = TelephonyCampaignService(repository=self.repository)
            attempts = await service.list_call_attempts(campaign_id=campaign_id, lead_id=lead_id, limit=limit)
            return ConsoleActionResult(
                action="list_call_attempts",
                success=True,
                message=f"Retrieved {len(attempts)} call attempts.",
                data={"attempts": attempts},
            )
        except Exception as e:
            return ConsoleActionResult(action="list_call_attempts", success=False, message="Failed to list call attempts.", error=str(e))

    async def mark_call_outcome(
        self, attempt_id: str, outcome: str, operator: str, metadata: dict | None = None
    ) -> ConsoleActionResult:
        try:
            from telephony.call_control import TelephonyCallControl
            control = TelephonyCallControl(repository=self.repository)
            res = await control.mark_call_outcome(attempt_id, outcome, operator, metadata=metadata)
            return self.result_from_model("mark_call_outcome", res, res.message)
        except Exception as e:
            return ConsoleActionResult(action="mark_call_outcome", success=False, message="Failed to mark call outcome.", error=str(e))

    async def end_live_call(self, call_session_id: str, operator: str, reason: str) -> ConsoleActionResult:
        try:
            from telephony.call_control import TelephonyCallControl
            control = TelephonyCallControl(repository=self.repository)
            res = await control.end_call(call_session_id, operator, reason)
            return self.result_from_model("end_live_call", res, res.message)
        except Exception as e:
            return ConsoleActionResult(action="end_live_call", success=False, message="Failed to end live call.", error=str(e))

    async def export_call_attempt_to_training(self, attempt_id: str, operator: str) -> ConsoleActionResult:
        try:
            from telephony.call_control import TelephonyCallControl
            control = TelephonyCallControl(repository=self.repository)
            res = await control.export_call_to_training(attempt_id, operator)
            return self.result_from_model("export_call_attempt_to_training", res, res.message)
        except Exception as e:
            return ConsoleActionResult(action="export_call_attempt_to_training", success=False, message="Failed to export call to training.", error=str(e))

    async def get_telephony_campaign_analytics(self, campaign_id: str) -> ConsoleActionResult:
        try:
            from telephony.telephony_reports import TelephonyReports
            reports = TelephonyReports(repository=self.repository)
            res = await reports.get_campaign_analytics(campaign_id)
            return ConsoleActionResult(
                action="get_telephony_campaign_analytics",
                success=True,
                message="Campaign analytics generated.",
                data={"analytics": res},
            )
        except Exception as e:
            return ConsoleActionResult(action="get_telephony_campaign_analytics", success=False, message="Failed to generate campaign analytics.", error=str(e))


def json_serializable(obj: Any) -> Any:
    """Recursively convert custom objects/dataclasses to JSON-serializable formats."""
    if type(obj).__name__ in ("Mock", "MagicMock", "AsyncMock"):
        return {}
    if hasattr(obj, "model_dump"):
        return json_serializable(obj.model_dump(mode="json"))
    if hasattr(obj, "__dataclass_fields__"):
        return {k: json_serializable(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, list):
        return [json_serializable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: json_serializable(v) for k, v in obj.items()}
    return obj

