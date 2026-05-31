"""YouTube Transcript Importer for Dana's continuous training system.

Imports local transcript files or manifests of YouTube transcripts (offline-only).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field

from storage.repository import Repository


class YouTubeTranscriptImportConfig(BaseModel):
    """Configuration for importing YouTube transcripts."""

    output_dir: str = "data/imports/youtube_training"
    source_url: Optional[str] = None
    title: Optional[str] = None
    transcript_file: Optional[str] = None
    transcript_text: Optional[str] = None
    manifest_path: Optional[str] = None
    run_intake_after_import: bool = False
    dry_run: bool = False
    sanitize_filename: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class YouTubeTranscriptImportItemResult(BaseModel):
    """Result of importing a single transcript item."""

    title: str
    source_url: Optional[str] = None
    output_path: Optional[str] = None
    status: Literal["imported", "skipped", "failed", "dry_run"]
    character_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    intake_result: Optional[dict[str, Any]] = None


class YouTubeTranscriptImportResult(BaseModel):
    """Result of a YouTube transcript import run."""

    import_id: str
    total_items: int = 0
    imported_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    dry_run: bool
    item_results: list[YouTubeTranscriptImportItemResult] = Field(default_factory=list)
    report_json_path: Optional[str] = None
    report_markdown_path: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class YouTubeTranscriptImporter:
    """Imports offline YouTube transcript material into standard folders and triggers intake."""

    def __init__(self, repository: Repository | None = None, orchestrator: Any = None) -> None:
        self.repository = repository or Repository()
        self._orchestrator = orchestrator

    def load_manifest(self, manifest_path: str | Path) -> list[dict[str, Any]]:
        """Loads a JSON manifest file containing video transcript listings."""
        p = Path(manifest_path)
        if not p.exists():
            raise ValueError(f"Manifest file not found: {manifest_path}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"Failed to parse manifest JSON: {e}")
        
        return data.get("videos") or []

    def load_transcript_text(self, transcript_file: str | Path | None, transcript_text: str | None) -> str:
        """Loads transcript text either from a string or file."""
        if transcript_text:
            return transcript_text.strip()
        if transcript_file:
            p = Path(transcript_file)
            if not p.exists():
                raise ValueError(f"Transcript file not found: {transcript_file}")
            return p.read_text(encoding="utf-8").strip()
        return ""

    def sanitize_title_to_filename(self, title: str) -> str:
        """Converts a title string to a safe filesystem filename."""
        safe = re.sub(r"[^\w\-_\s]", "", title)
        safe = re.sub(r"\s+", "_", safe)
        return safe.lower().strip("_")

    def build_transcript_document(self, title: str, transcript_text: str, source_url: str | None, metadata: dict | None = None) -> str:
        """Formats the output file containing metadata front matter and content."""
        meta = metadata or {}
        now_str = datetime.now(timezone.utc).isoformat()
        
        doc = f"""---
source_type: youtube
title: {title}
source_url: {source_url or "N/A"}
imported_at: {now_str}
"""
        for k, v in meta.items():
            doc += f"{k}: {v}\n"
        doc += f"""---
{transcript_text}
"""
        return doc

    async def maybe_run_intake(self, output_path: str | Path, config: YouTubeTranscriptImportConfig) -> dict | None:
        """Triggers the intake orchestrator on the imported file, if enabled."""
        if not config.run_intake_after_import:
            return None

        # Lazy load to avoid circular dependencies
        from training.intake_orchestrator import TrainingIntakeOrchestrator, TrainingIntakeConfig
        
        orch = self._orchestrator or TrainingIntakeOrchestrator(repository=self.repository)
        
        intake_config = TrainingIntakeConfig(
            mode="folder",
            input_path=str(Path(output_path).parent),
            source_type="youtube",
            dry_run=config.dry_run,
            label_after_ingest=True,
            mine_after_label=True,
        )
        
        # Search the run items to find the result matching this file
        run_res = await orch.run(intake_config)
        
        # Extract matching item result if found
        filename = Path(output_path).name
        from pydantic import BaseModel
        if isinstance(run_res, BaseModel):
            for item in getattr(run_res, "item_results", []):
                if getattr(item, "item_id", None) == filename:
                    return item.model_dump(mode="json")
            return run_res.model_dump(mode="json")
        elif isinstance(run_res, dict):
            for item in run_res.get("item_results", []):
                if isinstance(item, dict) and item.get("item_id") == filename:
                    return item
            return run_res
        return {}

    async def import_one(self, title: str, transcript_text: str, source_url: str | None, config: YouTubeTranscriptImportConfig) -> YouTubeTranscriptImportItemResult:
        """Processes and writes a single transcript item."""
        if not title:
            title = f"Youtube Video {uuid.uuid4().hex[:8]}"

        result = YouTubeTranscriptImportItemResult(
            title=title,
            source_url=source_url,
            status="failed",
        )

        try:
            if not transcript_text:
                raise ValueError("Transcript text cannot be empty.")

            result.character_count = len(transcript_text)
            
            filename = self.sanitize_title_to_filename(title) if config.sanitize_filename else title
            if not filename.endswith(".txt") and not filename.endswith(".md"):
                filename += ".txt"

            out_dir = Path(config.output_dir)
            output_path = out_dir / filename

            if config.dry_run:
                result.status = "dry_run"
                result.output_path = str(output_path)
                return result

            # Build and write front matter doc
            doc_content = self.build_transcript_document(title, transcript_text, source_url, config.metadata)
            
            out_dir.mkdir(parents=True, exist_ok=True)
            output_path.write_text(doc_content, encoding="utf-8")
            
            result.output_path = str(output_path.resolve()).replace("\\", "/")
            result.status = "imported"

            # Downstream intake
            intake_res = await self.maybe_run_intake(output_path, config)
            result.intake_result = intake_res

        except Exception as e:
            result.status = "failed"
            result.error = str(e)

        return result

    async def import_transcripts(self, config: YouTubeTranscriptImportConfig) -> YouTubeTranscriptImportResult:
        """Bulk imports YouTube transcripts from files, strings, or manifests."""
        import_id = f"youtube_import_{uuid.uuid4().hex[:8]}"
        run_result = YouTubeTranscriptImportResult(
            import_id=import_id,
            dry_run=config.dry_run,
        )

        items_to_import = []

        try:
            # 1. Manifest import
            if config.manifest_path:
                manifest_items = self.load_manifest(config.manifest_path)
                manifest_dir = Path(config.manifest_path).parent
                
                for idx, item in enumerate(manifest_items):
                    t_title = item.get("title") or f"Manifest Video {idx + 1}"
                    t_url = item.get("url")
                    
                    raw_text = item.get("transcript")
                    raw_file = item.get("transcript_file")
                    
                    if raw_file and not Path(raw_file).is_absolute() and manifest_dir:
                        raw_file = str(manifest_dir / raw_file)
                        
                    try:
                        text = self.load_transcript_text(raw_file, raw_text)
                        items_to_import.append((t_title, text, t_url))
                    except Exception as e:
                        # Log warning or add failed item result directly
                        run_result.item_results.append(YouTubeTranscriptImportItemResult(
                            title=t_title,
                            source_url=t_url,
                            status="failed",
                            error=str(e),
                        ))
            
            # 2. Direct script import
            elif config.transcript_text is not None or config.transcript_file is not None:
                title = config.title or "YouTube Transcript"
                text = self.load_transcript_text(config.transcript_file, config.transcript_text)
                items_to_import.append((title, text, config.source_url))

            else:
                raise ValueError("Must provide either manifest_path, transcript_text, or transcript_file.")

        except Exception as e:
            run_result.warnings.append(f"Import initialization failure: {e}")

        # Import all discovered items
        for title, text, url in items_to_import:
            item_res = await self.import_one(title, text, url, config)
            run_result.item_results.append(item_res)

        # Compute summary
        run_result.total_items = len(run_result.item_results)
        for item in run_result.item_results:
            if item.status == "imported":
                run_result.imported_count += 1
            elif item.status == "skipped":
                run_result.skipped_count += 1
            elif item.status == "failed":
                run_result.failed_count += 1
            elif item.status == "dry_run":
                run_result.imported_count += 1

        # Write reports
        if run_result.total_items > 0 or len(run_result.warnings) > 0:
            json_p, md_p = self.write_report(run_result, config.output_dir)
            run_result.report_json_path = json_p
            run_result.report_markdown_path = md_p

        return run_result

    def write_report(self, result: YouTubeTranscriptImportResult, output_dir: str | Path) -> tuple[str, str]:
        """Generates JSON and Markdown summaries of the YouTube import run."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        json_file = out / f"youtube_import_{result.import_id}.json"
        md_file = out / f"youtube_import_{result.import_id}.md"

        # JSON
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(mode="json"), f, indent=2)

        # Markdown
        md_content = f"""# YouTube Transcript Import Report

Import ID: {result.import_id}
Dry run: {result.dry_run}

## Executive Summary
- Total items: {result.total_items}
- Imported: {result.imported_count}
- Skipped: {result.skipped_count}
- Failed: {result.failed_count}

## Item Results
| Status | Title | Source URL | Output Path | Character Count | Warnings/Error |
| --- | --- | --- | --- | --- | --- |
"""
        for r in result.item_results:
            err_msg = r.error or (", ".join(r.warnings) if r.warnings else "None")
            md_content += f"| {r.status.upper()} | {r.title} | {r.source_url or 'N/A'} | {r.output_path or 'N/A'} | {r.character_count} | {err_msg} |\n"

        md_content += """
## Safety Notes
- **No auto-approval performed**: All imported transcripts must be processed through continuous training labeling and example mining gates.
- **No prompt edits performed**: The live final expense prompts were not modified.
- **No fine-tuning started**: External provider fine-tuning endpoints were not called.
- **No provider upload**: Dataset artifacts were saved local-only.
- **No network calls**: Ingestion was executed 100% offline; no YouTube or external API lookups were made.

## Next Steps
1. Run the Training Intake Orchestrator on the imported directory (if not auto-run).
2. Human-review mined example candidates inside the continuous QA queue.
3. Promote prompts or construct datasets only after safety validation gates pass.
"""
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md_content)

        return (
            str(json_file.resolve()).replace("\\", "/"),
            str(md_file.resolve()).replace("\\", "/"),
        )
