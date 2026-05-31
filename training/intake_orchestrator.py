"""Automatic Training Intake Orchestrator for Dana's continuous training system.

Connects and orchestrates ingestion, labeling, and example mining workflows.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field

from storage.repository import Repository
from training.ingestion import TrainingIngestionService, redact_text
from training.labeler import TranscriptLabeler
from training.example_miner import TrainingExampleMiner
from training.daily_qa_miner import DailyQaMiner


class TrainingIntakeConfig(BaseModel):
    """Configuration for a training intake run."""

    mode: Literal["post_call", "folder", "manifest", "daily"]
    source_type: Optional[str] = None
    input_path: Optional[str] = None
    folders: list[str] = Field(default_factory=lambda: [
        "data/imports/call_transcripts",
        "data/imports/youtube_training",
        "data/imports/manager_notes",
        "data/imports/licensed_agent_feedback",
        "data/imports/post_call_payloads",
    ])
    manifest_path: Optional[str] = None
    output_dir: str = "data/intake_reports"
    state_path: str = "data/intake_reports/intake_state.json"
    label_after_ingest: bool = True
    mine_after_label: bool = True
    run_daily_qa: bool = False
    move_processed: bool = False
    continue_on_error: bool = True
    limit: Optional[int] = None
    dry_run: bool = False
    since: Optional[str] = None
    report_markdown: bool = True


class TrainingIntakeItemResult(BaseModel):
    """Result of processing a single intake item."""

    item_id: str
    source_type: str
    input_path: Optional[str] = None
    title: Optional[str] = None
    status: Literal["ingested", "duplicate", "skipped", "failed", "dry_run"]
    training_source_id: Optional[str] = None
    labeled: bool = False
    mined: bool = False
    review_items_created: int = 0
    duplicate_of: Optional[str] = None
    error: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrainingIntakeRunResult(BaseModel):
    """Result of a training intake orchestration run."""

    run_id: str
    mode: str
    started_at: str
    ended_at: str
    dry_run: bool
    total_items: int = 0
    ingested_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    labeled_count: int = 0
    mined_count: int = 0
    review_items_created: int = 0
    daily_qa_ran: bool = False
    daily_qa_summary: dict[str, Any] = Field(default_factory=dict)
    item_results: list[TrainingIntakeItemResult] = Field(default_factory=list)
    report_json_path: Optional[str] = None
    report_markdown_path: Optional[str] = None
    state_path: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class TrainingIntakeOrchestrator:
    """Orchestrates continuous training intake pipelines."""

    def __init__(
        self,
        repository: Repository | None = None,
        ingestion_service: TrainingIngestionService | None = None,
        labeler: TranscriptLabeler | None = None,
        miner: TrainingExampleMiner | None = None,
        daily_qa_miner: DailyQaMiner | None = None,
    ):
        self.repository = repository or Repository()
        self.ingestion_service = ingestion_service or TrainingIngestionService(self.repository)
        self.labeler = labeler or TranscriptLabeler(self.repository)
        self.miner = miner or TrainingExampleMiner(self.repository)
        self.daily_qa_miner = daily_qa_miner or DailyQaMiner(self.repository)

    def _get_content_hash(self, input_data: Any) -> str:
        """Computes SHA-256 content hash matching ingestion service logic."""
        from training.ingestion import normalize_turns
        normalized_turns = normalize_turns(input_data)
        canonical_turns = json.dumps(normalized_turns, sort_keys=True)
        return hashlib.sha256(canonical_turns.encode("utf-8")).hexdigest()

    async def _check_duplicate(self, input_data: Any) -> tuple[bool, str | None]:
        """Queries repository to check if content hash is already ingested."""
        content_hash = self._get_content_hash(input_data)
        recent = await self.repository.list_recent_training_sources(limit=5000)
        for src in recent:
            src_meta = src.get("metadata") or {}
            if src_meta.get("content_hash") == content_hash:
                return True, src["id"]
        return False, None

    async def run(self, config: TrainingIntakeConfig) -> TrainingIntakeRunResult:
        """Runs the intake process based on the configured mode."""
        started_at = datetime.now(timezone.utc).isoformat()
        run_id = f"intake_{uuid.uuid4().hex[:8]}"
        run_result = TrainingIntakeRunResult(
            run_id=run_id,
            mode=config.mode,
            started_at=started_at,
            ended_at="",
            dry_run=config.dry_run,
        )

        item_results: list[TrainingIntakeItemResult] = []

        try:
            if config.mode == "post_call":
                if not config.input_path:
                    raise ValueError("input_path (payload JSON file) must be provided in post_call mode.")
                p = Path(config.input_path)
                if not p.exists():
                    raise ValueError(f"Payload file not found: {config.input_path}")
                payload = json.loads(p.read_text(encoding="utf-8"))
                res = await self.ingest_post_call_payload(payload, config)
                item_results.append(res)

            elif config.mode == "folder":
                if not config.input_path:
                    raise ValueError("input_path (folder directory) must be provided in folder mode.")
                results = await self.ingest_folder(config.input_path, config.source_type, config)
                item_results.extend(results)

            elif config.mode == "manifest":
                if not config.manifest_path:
                    raise ValueError("manifest_path must be provided in manifest mode.")
                results = await self.ingest_manifest(config.manifest_path, config)
                item_results.extend(results)

            elif config.mode == "daily":
                # 1. Scan configured standard folders
                for folder_str in config.folders:
                    folder_path = Path(folder_str)
                    if folder_path.exists() and folder_path.is_dir():
                        inferred_type = self.infer_source_type_from_path(folder_path)
                        results = await self.ingest_folder(folder_path, inferred_type, config)
                        item_results.extend(results)

                # 2. Daily QA miner
                if config.run_daily_qa:
                    run_result.daily_qa_ran = True
                    run_result.daily_qa_summary = await self.run_daily_qa_if_enabled(config)

        except Exception as e:
            if not config.continue_on_error:
                raise e
            run_result.warnings.append(f"Intake mode failure: {e}")

        # Update run metrics
        run_result.item_results = item_results
        run_result.total_items = len(item_results)

        for item in item_results:
            if item.status == "ingested":
                run_result.ingested_count += 1
            elif item.status == "duplicate":
                run_result.duplicate_count += 1
            elif item.status == "skipped":
                run_result.skipped_count += 1
            elif item.status == "failed":
                run_result.failed_count += 1
            elif item.status == "dry_run":
                run_result.ingested_count += 1  # count dry run ingest as candidate

            if item.labeled:
                run_result.labeled_count += 1
            if item.mined:
                run_result.mined_count += 1
            run_result.review_items_created += item.review_items_created

        run_result.ended_at = datetime.now(timezone.utc).isoformat()

        # Write reports
        if config.output_dir:
            json_p, md_p = self.write_report(run_result, config.output_dir)
            run_result.report_json_path = json_p
            run_result.report_markdown_path = md_p

        # Save processed states
        if config.state_path and not config.dry_run:
            state = self.load_state(config.state_path)
            for item in item_results:
                if item.input_path and item.status in ("ingested", "duplicate", "skipped"):
                    state[str(Path(item.input_path).resolve()).replace("\\", "/")] = {
                        "file_hash": self.compute_file_hash(item.input_path),
                        "source_type": item.source_type,
                        "last_processed_at": datetime.now(timezone.utc).isoformat(),
                        "training_source_id": item.training_source_id,
                        "status": item.status,
                        "error": item.error,
                    }
            self.save_state(config.state_path, state)
            run_result.state_path = str(Path(config.state_path).resolve()).replace("\\", "/")

        return run_result

    async def ingest_post_call_payload(self, payload: dict, config: TrainingIntakeConfig) -> TrainingIntakeItemResult:
        """Ingests a completed post-call payload dictionary."""
        item_id = payload.get("call_id") or f"payload_{uuid.uuid4().hex[:8]}"
        result = TrainingIntakeItemResult(
            item_id=item_id,
            source_type="post_call",
            title=f"Post-Call {payload.get('call_id', 'unknown')}",
            status="failed",
        )

        try:
            normalized = self.normalize_post_call_payload(payload)
            title = normalized["title"]
            result.title = title
            meta = normalized["metadata"]

            # Serialize content representing turns
            turns = normalized.get("turns")
            transcript = normalized.get("transcript")

            # Check duplicates using unified hash helper
            duplicate_detected, duplicate_of = await self._check_duplicate(turns or transcript or "")

            if duplicate_detected:
                result.status = "duplicate"
                result.training_source_id = duplicate_of
                result.duplicate_of = duplicate_of
                return result

            if config.dry_run:
                result.status = "dry_run"
                return result

            # Ingest
            ingest_res = await self.ingestion_service.ingest_source(
                source_type="post_call",
                title=title,
                content=json.dumps({"turns": turns}) if turns else transcript,
                metadata=meta,
            )

            result.status = "ingested"
            result.training_source_id = ingest_res.source_id

            # Process downstream (Label/Mine)
            labeled, mined, created = await self.process_training_source(ingest_res.source_id, config)
            result.labeled = labeled
            result.mined = mined
            result.review_items_created = created

        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            if not config.continue_on_error:
                raise e

        return result

    async def ingest_file(self, path: str | Path, source_type: str, config: TrainingIntakeConfig) -> TrainingIntakeItemResult:
        """Ingests a single file."""
        p = Path(path)
        item_id = p.name
        title = self.build_title(p, source_type)

        result = TrainingIntakeItemResult(
            item_id=item_id,
            source_type=source_type,
            input_path=str(p),
            title=title,
            status="failed",
        )

        try:
            # Check skip
            if config.state_path:
                state = self.load_state(config.state_path)
                should_skip, reason = self.should_skip_file(p, source_type, state)
                if should_skip:
                    result.status = "skipped"
                    result.warnings.append(reason or "Skipped via state check.")
                    # retrieve past id if possible
                    p_resolved = str(p.resolve()).replace("\\", "/")
                    if p_resolved in state:
                        result.training_source_id = state[p_resolved].get("training_source_id")
                    return result

            # Read content to check duplicates before write
            content = p.read_text(encoding="utf-8")

            # Handle case where file is a post-call payload JSON
            if source_type == "post_call" and p.suffix.lower() == ".json":
                payload = json.loads(content)
                res = await self.ingest_post_call_payload(payload, config)
                res.input_path = str(p)
                return res

            # Parse content turns to check duplicate
            parsed_content = content
            if p.suffix.lower() == ".json":
                try:
                    from training.ingestion import extract_turns_from_json
                    parsed_content = extract_turns_from_json(json.loads(content))
                except Exception:
                    pass
            elif p.suffix.lower() == ".jsonl":
                try:
                    from training.ingestion import extract_turns_from_jsonl
                    parsed_content = extract_turns_from_jsonl(content)
                except Exception:
                    pass

            duplicate_detected, duplicate_of = await self._check_duplicate(parsed_content)

            if duplicate_detected:
                result.status = "duplicate"
                result.training_source_id = duplicate_of
                result.duplicate_of = duplicate_of
                if config.move_processed and not config.dry_run:
                    self.move_file_safe(p, Path("data/imports/processed"))
                return result

            if config.dry_run:
                result.status = "dry_run"
                return result

            # Ingest
            ingest_res = await self.ingestion_service.ingest_source(
                source_type=source_type,
                title=title,
                file_path=p,
            )

            result.status = "ingested"
            result.training_source_id = ingest_res.source_id

            # Downstream process
            labeled, mined, created = await self.process_training_source(ingest_res.source_id, config)
            result.labeled = labeled
            result.mined = mined
            result.review_items_created = created

            # Move processed
            if config.move_processed:
                self.move_file_safe(p, Path("data/imports/processed"))

        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            if config.move_processed and not config.dry_run:
                try:
                    self.move_file_safe(p, Path("data/imports/failed"))
                except Exception:
                    pass
            if not config.continue_on_error:
                raise e

        return result

    async def ingest_folder(self, folder: str | Path, source_type: str | None, config: TrainingIntakeConfig) -> list[TrainingIntakeItemResult]:
        """Scans a directory recursively and ingests discovered files."""
        p = Path(folder)
        if not p.exists() or not p.is_dir():
            return []

        files = self.discover_files(p, limit=config.limit)
        results = []

        for f in files:
            t = source_type or self.infer_source_type_from_path(f)
            res = await self.ingest_file(f, t, config)
            results.append(res)

        return results

    async def ingest_manifest(self, manifest_path: str | Path, config: TrainingIntakeConfig) -> list[TrainingIntakeItemResult]:
        """Reads a JSON manifest listing items and processes them."""
        p = Path(manifest_path)
        if not p.exists():
            raise ValueError(f"Manifest file not found: {manifest_path}")

        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"Failed to parse manifest JSON: {e}")

        items = data.get("items") or []
        results = []

        for idx, item in enumerate(items):
            item_type = item.get("type", "unknown")
            item_title = item.get("title")
            item_file = item.get("file")
            item_payload = item.get("payload")

            item_result = TrainingIntakeItemResult(
                item_id=f"manifest_item_{idx}",
                source_type=item_type,
                title=item_title,
                status="failed",
            )

            try:
                if item_file:
                    file_path = Path(item_file)
                    if not file_path.is_absolute() and p.parent:
                        file_path = p.parent / file_path
                    res = await self.ingest_file(file_path, item_type, config)
                    if item_title:
                        res.title = item_title
                    results.append(res)
                elif item_payload:
                    res = await self.ingest_post_call_payload(item_payload, config)
                    res.source_type = item_type
                    if item_title:
                        res.title = item_title
                    results.append(res)
                else:
                    item_result.error = "Manifest item must define either 'file' or 'payload'."
                    results.append(item_result)
            except Exception as e:
                item_result.error = str(e)
                results.append(item_result)
                if not config.continue_on_error:
                    raise e

        return results

    async def process_training_source(self, source_id: str, config: TrainingIntakeConfig) -> tuple[bool, bool, int]:
        """Runs downstream labeling and mining on a successfully ingested source."""
        labeled = False
        mined = False
        review_items_created = 0

        # Labeling
        if config.label_after_ingest:
            try:
                await self.labeler.label_training_source(source_id)
                labeled = True
            except Exception as e:
                labeled = False

        # Mining
        if config.mine_after_label and labeled:
            try:
                mine_res = await self.miner.mine_source(source_id)
                mined = True
                review_items_created = mine_res.candidates_created
            except Exception as e:
                mined = False

        return labeled, mined, review_items_created

    def discover_files(self, folder: str | Path, limit: int | None = None) -> list[Path]:
        """Discovers files under a folder matching supported extensions."""
        p = Path(folder)
        if not p.exists() or not p.is_dir():
            return []

        extensions = [".txt", ".json", ".jsonl", ".md"]
        files = []
        for root, _, filenames in os.walk(p):
            for f in filenames:
                file_path = Path(root) / f
                if file_path.suffix.lower() in extensions:
                    files.append(file_path)

        files.sort()
        if limit is not None:
            files = files[:limit]
        return files

    def infer_source_type_from_path(self, path: str | Path, fallback: str | None = None) -> str:
        """Infers the source type based on folder structures."""
        p = Path(path)
        path_str = str(p.resolve()).replace("\\", "/")

        if "call_transcripts" in path_str:
            return "call_transcript"
        elif "youtube_training" in path_str:
            return "youtube"
        elif "manager_notes" in path_str:
            return "manager_note"
        elif "licensed_agent_feedback" in path_str:
            return "licensed_agent_feedback"
        elif "post_call_payloads" in path_str:
            return "post_call"

        if fallback:
            return fallback
        return "unknown"

    def load_state(self, state_path: str | Path) -> dict:
        """Loads processed state database from a file."""
        p = Path(state_path)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_state(self, state_path: str | Path, state: dict) -> None:
        """Saves processed state database to a file."""
        p = Path(state_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass

    def should_skip_file(self, path: str | Path, source_type: str, state: dict) -> tuple[bool, str | None]:
        """Validates if a file should be skipped to prevent double ingestion."""
        p = Path(path)
        path_str = str(p.resolve()).replace("\\", "/")

        # 1. Path matches
        if path_str in state:
            entry = state[path_str]
            if entry.get("status") in ("ingested", "duplicate", "skipped"):
                return True, f"File path already processed with status: {entry.get('status')}"

        # 2. Content hash matches
        file_hash = self.compute_file_hash(p)
        if file_hash:
            for k, entry in state.items():
                if entry.get("file_hash") == file_hash:
                    if entry.get("status") in ("ingested", "duplicate", "skipped"):
                        return True, f"File content hash already processed under path {k} with status: {entry.get('status')}"

        return False, None

    def compute_file_hash(self, path: str | Path) -> str:
        """Computes SHA-256 hash of a file."""
        p = Path(path)
        if not p.exists() or p.is_dir():
            return ""
        try:
            content = p.read_bytes()
            return hashlib.sha256(content).hexdigest()
        except Exception:
            return ""

    def build_title(self, path_or_payload: Any, source_type: str) -> str:
        """Generates descriptive title for ingested material."""
        if isinstance(path_or_payload, (str, Path)):
            return Path(path_or_payload).stem.replace("_", " ").title()
        elif isinstance(path_or_payload, dict):
            call_id = path_or_payload.get("call_id") or path_or_payload.get("metadata", {}).get("call_id") or "unknown"
            return f"Post-Call {call_id}"
        return f"{source_type.replace('_', ' ').title()} Source"

    def normalize_post_call_payload(self, payload: dict) -> dict:
        """Redacts phone and normalizes completed call structure."""
        phone = payload.get("prospect_phone")
        redacted_phone = "[REDACTED_PHONE]"
        if phone:
            redacted_phone, _ = redact_text(phone)

        # Base metadata
        meta = {
            "call_id": payload.get("call_id"),
            "outcome": payload.get("outcome"),
            "transfer_consent": payload.get("transfer_consent"),
            "direction": payload.get("direction"),
            "campaign": payload.get("campaign"),
        }
        if "recording_url" in payload:
            meta["recording_url"] = payload["recording_url"]
        if "tool_events" in payload:
            meta["tool_events_summary"] = payload["tool_events"]
        if "qa" in payload:
            meta["qa_summary"] = payload["qa"]

        # Preserve other payload fields under metadata
        payload_meta = payload.get("metadata") or {}
        for k, v in payload_meta.items():
            if k not in meta:
                meta[k] = v

        normalized_payload = {
            "source_type": "post_call",
            "title": f"Post-Call {payload.get('call_id', 'unknown')}",
            "metadata": meta,
            "prospect_phone": redacted_phone,
        }

        turns = payload.get("turns")
        transcript = payload.get("transcript")
        if turns:
            normalized_payload["turns"] = turns
        elif transcript:
            normalized_payload["transcript"] = transcript

        return normalized_payload

    async def run_daily_qa_if_enabled(self, config: TrainingIntakeConfig) -> dict:
        """Fires off Daily QA Miner analysis safely."""
        target_date = config.since
        if not target_date:
            target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            res = await self.daily_qa_miner.mine_date(target_date, dry_run=config.dry_run)
            return res.model_dump(mode="json")
        except Exception as e:
            return {
                "error": str(e),
                "warnings": [f"Daily QA Miner failed: {e}"]
            }

    def write_report(self, result: TrainingIntakeRunResult, output_dir: str | Path) -> tuple[str, str | None]:
        """Writes JSON and Markdown summaries."""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        json_file = out_path / f"training_intake_{result.run_id}.json"
        md_file = out_path / f"training_intake_{result.run_id}.md"

        # JSON
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(mode="json"), f, indent=2)

        # Markdown
        # Compute breakdown by type
        breakdown = {}
        for r in result.item_results:
            stats = breakdown.setdefault(r.source_type, {"total": 0, "ingested": 0, "duplicate": 0, "failed": 0, "mined": 0})
            stats["total"] += 1
            if r.status in ("ingested", "dry_run"):
                stats["ingested"] += 1
            elif r.status == "duplicate":
                stats["duplicate"] += 1
            elif r.status == "failed":
                stats["failed"] += 1
            stats["mined"] += r.review_items_created

        md_content = f"""# Dana Training Intake Report

Run ID: {result.run_id}
Mode: {result.mode}
Started: {result.started_at}
Ended: {result.ended_at}
Dry run: {result.dry_run}

## Executive Summary
- Total items processed: {result.total_items}
- Ingested: {result.ingested_count}
- Duplicates: {result.duplicate_count}
- Skipped: {result.skipped_count}
- Failed: {result.failed_count}
- Labeled: {result.labeled_count}
- Mined: {result.mined_count}
- Review items created: {result.review_items_created}
- Daily QA ran: {result.daily_qa_ran}

## Source Breakdown
| Source Type | Total | Ingested | Duplicate | Failed | Review Items Created |
| --- | --- | --- | --- | --- | --- |
"""
        for t, b in breakdown.items():
            md_content += f"| {t} | {b['total']} | {b['ingested']} | {b['duplicate']} | {b['failed']} | {b['mined']} |\n"

        md_content += """
## Item Results
| Status | Source Type | Title/Input | Training Source ID | Labeled | Mined | Review Items | Warnings/Error |
| --- | --- | --- | --- | --- | --- | --- | --- |
"""
        for r in result.item_results:
            input_name = r.input_path or r.item_id
            err_msg = r.error or (", ".join(r.warnings) if r.warnings else "None")
            md_content += f"| {r.status.upper()} | {r.source_type} | {r.title or input_name} | {r.training_source_id or 'N/A'} | {r.labeled} | {r.mined} | {r.review_items_created} | {err_msg} |\n"

        if result.daily_qa_ran:
            md_content += f"""
## Daily QA Summary
- Status: Completed
- Failure Clusters Created: {result.daily_qa_summary.get("failure_clusters_created", 0)}
- Mined Candidates Created: {result.daily_qa_summary.get("winning_response_candidates_created", 0)}
- Human Review Items Ingested: {result.daily_qa_summary.get("human_review_items_created", 0)}
"""

        md_content += """
## Safety Notes
- **No auto-approval performed**: All mined training examples and coaching lessons are in a pending state.
- **No prompt edits performed**: The live prompt configuration file is untouched.
- **No fine-tuning started**: Provider fine-tuning uploads or start endpoints were not invoked.
- **No provider API calls**: Scan and ingestion processes were run 100% offline.
- **Human review required**: Mined examples must be explicitly reviewed and approved by an administrator.

## Next Steps
1. Review pending HumanReviewItems inside the coaching pipeline.
2. Approve or reject training example candidates.
3. Validate candidate models against offline eval/replay/simulation gates.
4. Promote prompt configurations using prompt version/canary lifecycles.
"""

        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md_content)

        return (
            str(json_file.resolve()).replace("\\", "/"),
            str(md_file.resolve()).replace("\\", "/")
        )

    def move_file_safe(self, src: Path, dest_dir: Path) -> Path:
        """Safely moves a file to a destination directory, resolving name collisions."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{src.save_stem}_{uuid.uuid4().hex[:6]}{src.suffix}" if hasattr(src, "save_stem") else dest_dir / f"{src.stem}_{uuid.uuid4().hex[:6]}{src.suffix}"
        shutil.move(str(src), str(dest))
        return dest
