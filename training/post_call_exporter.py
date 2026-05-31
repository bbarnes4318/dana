"""Post-Call Runtime Exporter for Dana's continuous training system.

Redacts direct identifiers and exports completed call payloads.
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
from training.ingestion import redact_text, normalize_turns


class PostCallExportConfig(BaseModel):
    """Configuration for a post-call runtime export."""

    enabled: bool = False
    output_dir: str = "data/imports/post_call_payloads"
    source: str = "agent_runtime"
    redact_direct_identifiers: bool = True
    include_recording_url: bool = True
    include_tool_events: bool = True
    include_qa: bool = True
    include_metadata: bool = True
    run_intake_after_export: bool = False
    intake_sync: bool = False
    fail_silently: bool = True
    max_turns: Optional[int] = None
    dry_run: bool = False


class PostCallTurn(BaseModel):
    """A single dialogue turn in the exported call."""

    speaker: Literal["agent", "prospect", "system", "tool", "unknown"]
    text: str
    timestamp: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PostCallPayload(BaseModel):
    """Intake-compatible payload representing a completed call."""

    call_id: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    direction: Optional[str] = None
    campaign: Optional[str] = None
    prospect_phone: Optional[str] = None
    recording_url: Optional[str] = None
    outcome: Optional[str] = None
    transfer_consent: bool = False
    transcript: Optional[str] = None
    turns: list[PostCallTurn] = Field(default_factory=list)
    tool_events: list[dict[str, Any]] = Field(default_factory=list)
    qa: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PostCallExportResult(BaseModel):
    """Outcome of exporting a completed call."""

    exported: bool
    dry_run: bool
    call_id: Optional[str] = None
    output_path: Optional[str] = None
    intake_ran: bool = False
    intake_result: Optional[dict[str, Any]] = None
    redactions: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class PostCallExporter:
    """Safely exports runtime call state to JSON files for training ingestion."""

    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository or Repository()

    def redact_payload(self, payload: dict, config: PostCallExportConfig) -> tuple[dict, dict]:
        """Redacts sensitive direct identifiers (phone, email, SSN, cards, DOB, Medicare)."""
        redacted = dict(payload)
        redactions = {}

        if not config.redact_direct_identifiers:
            return redacted, redactions

        # 1. Redact phone
        phone = redacted.get("prospect_phone")
        if phone and phone != "[REDACTED_PHONE]":
            redacted["prospect_phone"] = "[REDACTED_PHONE]"
            redactions["prospect_phone"] = True

        # Helper to redact a string for other identifiers (DOB, Medicare, and general PII)
        def redact_str(text: str) -> tuple[str, bool]:
            if not text:
                return text, False
            
            modified = False
            # Standard redaction (email, SSN, cards, phone, bank rtn, accounts)
            clean_text, count = redact_text(text)
            if count > 0:
                modified = True

            # Custom DOB redaction (dob, birth, born, date of birth) -> [REDACTED_DOB]
            dob_pattern = re.compile(
                r"\b(?:dob|birth|born|date of birth)\b\s*(?:is|:|-)?\s*\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})\b",
                re.IGNORECASE
            )
            clean_text, dob_count = dob_pattern.subn("[REDACTED_DOB]", clean_text)
            if dob_count > 0:
                modified = True

            # Custom Medicare Beneficiary Identifier (MBI) -> [REDACTED_MEDICARE]
            # Standard 11-char MBI pattern
            med_pattern = re.compile(
                r"\b(?:medicare|medicare #|medicare number|mbi)\b\s*(?:is|:|-)?\s*\b([a-zA-Z0-9-]{11,15})\b",
                re.IGNORECASE
            )
            clean_text, med_count = med_pattern.subn("[REDACTED_MEDICARE]", clean_text)
            if med_count > 0:
                modified = True

            return clean_text, modified

        # Redact turns
        turns = redacted.get("turns") or []
        redacted_turns = []
        turn_redactions = 0
        for t in turns:
            t_dict = dict(t) if not isinstance(t, BaseModel) else t.model_dump()
            txt = t_dict.get("text", "")
            clean_txt, turn_mod = redact_str(txt)
            if turn_mod:
                turn_redactions += 1
                t_dict["text"] = clean_txt
            redacted_turns.append(t_dict)
        redacted["turns"] = redacted_turns
        if turn_redactions > 0:
            redactions["turns_redacted"] = turn_redactions

        # Redact raw transcript if present
        transcript = redacted.get("transcript")
        if transcript:
            clean_trans, trans_mod = redact_str(transcript)
            if trans_mod:
                redacted["transcript"] = clean_trans
                redactions["transcript_redacted"] = True

        return redacted, redactions

    def normalize_turns(self, turns: list[Any], max_turns: int | None = None) -> list[dict]:
        """Normalizes conversational turns to training input format."""
        norm = []
        for t in turns:
            if isinstance(t, dict):
                speaker = t.get("speaker") or "unknown"
                text = t.get("text") or ""
                ts = t.get("timestamp")
                meta = t.get("metadata") or {}
            elif hasattr(t, "speaker") and hasattr(t, "text"):
                speaker = getattr(t, "speaker")
                text = getattr(t, "text")
                ts = getattr(t, "timestamp", None)
                meta = getattr(t, "metadata", {}) or {}
            else:
                continue

            # Normalize speaker name
            speaker = str(speaker).lower().strip()
            if speaker in ("user", "prospect"):
                speaker = "prospect"
            elif speaker in ("agent", "assistant"):
                speaker = "agent"
            elif speaker in ("system", "tool"):
                speaker = "system"
            else:
                speaker = "unknown"

            norm.append({
                "speaker": speaker,
                "text": text,
                "timestamp": str(ts) if ts is not None else None,
                "metadata": dict(meta) if meta else {},
            })

        if max_turns is not None:
            norm = norm[:max_turns]
        return norm

    def normalize_payload(self, payload: dict | PostCallPayload, config: PostCallExportConfig) -> dict:
        """Standardizes inputs to PostCallPayload format dictionary."""
        p_dict = payload.model_dump() if isinstance(payload, PostCallPayload) else dict(payload)

        call_id = p_dict.get("call_id") or f"call_{uuid.uuid4().hex[:8]}"
        
        # Build normalized base structure
        norm = {
            "call_id": call_id,
            "started_at": p_dict.get("started_at"),
            "ended_at": p_dict.get("ended_at"),
            "direction": p_dict.get("direction", "outbound"),
            "campaign": p_dict.get("campaign"),
            "prospect_phone": p_dict.get("prospect_phone"),
            "recording_url": p_dict.get("recording_url") if config.include_recording_url else None,
            "outcome": p_dict.get("outcome"),
            "transfer_consent": bool(p_dict.get("transfer_consent", False)),
            "transcript": p_dict.get("transcript"),
        }

        # Normalize turns
        raw_turns = p_dict.get("turns") or []
        norm["turns"] = self.normalize_turns(raw_turns, config.max_turns)

        # Normalize tool events
        if config.include_tool_events:
            norm["tool_events"] = [dict(evt) for evt in p_dict.get("tool_events") or []]
        else:
            norm["tool_events"] = []

        # Normalize QA
        if config.include_qa:
            norm["qa"] = dict(p_dict.get("qa") or {})
        else:
            norm["qa"] = {}

        # Normalize metadata
        if config.include_metadata:
            norm["metadata"] = dict(p_dict.get("metadata") or {})
        else:
            norm["metadata"] = {}

        return norm

    def build_output_path(self, call_id: str, output_dir: str | Path) -> Path:
        """Gets target output file path inside output_dir."""
        out = Path(output_dir)
        return out / f"{call_id}.json"

    def write_payload(self, payload: dict, path: str | Path) -> None:
        """Writes payload to disk."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    async def maybe_run_intake(self, output_path: str | Path, config: PostCallExportConfig) -> dict | None:
        """Calls the intake orchestrator to ingest the new file, if enabled."""
        if not config.run_intake_after_export:
            return None

        from training.intake_orchestrator import TrainingIntakeOrchestrator, TrainingIntakeConfig
        
        intake_config = TrainingIntakeConfig(
            mode="post_call",
            input_path=str(output_path),
            dry_run=config.dry_run,
            label_after_ingest=True,
            mine_after_label=True,
            continue_on_error=True,
        )
        
        orch = TrainingIntakeOrchestrator(repository=self.repository)
        
        if config.intake_sync:
            run_res = await orch.run(intake_config)
            from pydantic import BaseModel
            if isinstance(run_res, BaseModel):
                return run_res.model_dump(mode="json")
            elif isinstance(run_res, dict):
                return run_res
            return {}
        else:
            import asyncio
            asyncio.create_task(orch.run(intake_config))
            return {"status": "scheduled"}

    def payload_from_runtime_state(self, call_state: Any, metadata: dict | None = None) -> dict:
        """Helper to build payload from live call states."""
        metadata = metadata or {}
        payload = {}

        if isinstance(call_state, dict):
            payload = dict(call_state)
        elif hasattr(call_state, "state_machine") and hasattr(call_state, "events"):
            # Reconstruct from runtime
            lead = getattr(call_state.state_machine, "lead", None)
            cstate = getattr(call_state.state_machine, "call_state", None)

            if lead:
                payload["call_id"] = getattr(lead, "call_id", None)
                payload["campaign"] = getattr(lead, "campaign_id", None)
                payload["prospect_phone"] = getattr(lead, "lead_phone_e164", None)
                payload["transfer_consent"] = getattr(lead, "transfer_consent", False)
                if getattr(lead, "do_not_call_requested", False):
                    payload["outcome"] = "dnc"
                elif getattr(lead, "callback_requested", False):
                    payload["outcome"] = "callback"
                elif getattr(lead, "is_qualified", lambda: False)():
                    payload["outcome"] = "transfer"
                else:
                    payload["outcome"] = "unknown"

            if cstate:
                payload["started_at"] = getattr(cstate, "started_at", None)
                if payload["started_at"] and hasattr(payload["started_at"], "isoformat"):
                    payload["started_at"] = payload["started_at"].isoformat()
                payload["ended_at"] = getattr(cstate, "last_transition_at", None)
                if payload["ended_at"] and hasattr(payload["ended_at"], "isoformat"):
                    payload["ended_at"] = payload["ended_at"].isoformat()

            turns = []
            tool_events = []
            for event in getattr(call_state, "events", []):
                event_type = getattr(event, "event_type", None)
                ts = getattr(event, "timestamp", None)
                ts_str = ts.isoformat() if (ts and hasattr(ts, "isoformat")) else None

                if event_type == "utterance_received":
                    turns.append({
                        "speaker": "prospect",
                        "text": getattr(event, "text", ""),
                        "timestamp": ts_str,
                    })
                elif event_type == "response_generated":
                    turns.append({
                        "speaker": "agent",
                        "text": getattr(event, "text", ""),
                        "timestamp": ts_str,
                    })
                elif event_type == "tool_triggered":
                    tool_events.append({
                        "tool_name": getattr(event, "tool_name", ""),
                        "success": getattr(event, "success", True),
                        "result": getattr(event, "result_message", ""),
                        "timestamp": ts_str,
                    })
            payload["turns"] = turns
            payload["tool_events"] = tool_events
        elif hasattr(call_state, "current_stage") and hasattr(call_state, "started_at"):
            # Reconstruct from CallState
            payload["started_at"] = getattr(call_state, "started_at", None)
            if payload["started_at"] and hasattr(payload["started_at"], "isoformat"):
                payload["started_at"] = payload["started_at"].isoformat()
            payload["ended_at"] = getattr(call_state, "last_transition_at", None)
            if payload["ended_at"] and hasattr(payload["ended_at"], "isoformat"):
                payload["ended_at"] = payload["ended_at"].isoformat()

        # Merge metadata overrides
        for k, v in metadata.items():
            if v is not None:
                payload[k] = v

        return payload

    async def export_completed_call(self, payload: dict | PostCallPayload, config: PostCallExportConfig) -> PostCallExportResult:
        """Main entry point to serialize and redact a completed call payload."""
        warnings = []
        if not config.enabled:
            warnings.append("Post-call export disabled.")
            return PostCallExportResult(exported=False, dry_run=config.dry_run, warnings=warnings)

        norm_payload = self.normalize_payload(payload, config)
        call_id = norm_payload["call_id"]

        # Redact
        redacted_payload, redactions = self.redact_payload(norm_payload, config)

        output_path = self.build_output_path(call_id, config.output_dir)

        if config.dry_run:
            return PostCallExportResult(
                exported=True,
                dry_run=True,
                call_id=call_id,
                output_path=str(output_path),
                redactions=redactions,
                warnings=warnings,
            )

        # Write
        self.write_payload(redacted_payload, output_path)

        # Trigger Intake Orchestrator
        intake_res = await self.maybe_run_intake(output_path, config)

        return PostCallExportResult(
            exported=True,
            dry_run=False,
            call_id=call_id,
            output_path=str(output_path.resolve()).replace("\\", "/"),
            intake_ran=intake_res is not None,
            intake_result=intake_res,
            redactions=redactions,
            warnings=warnings,
        )

    async def safe_export_completed_call(self, payload: dict, config: PostCallExportConfig) -> PostCallExportResult:
        """Performs export capturing all exceptions, ensuring no impact on call runtimes."""
        try:
            return await self.export_completed_call(payload, config)
        except Exception as e:
            if not config.fail_silently:
                raise e
            return PostCallExportResult(
                exported=False,
                dry_run=config.dry_run,
                error=str(e),
                warnings=[f"Safe export catch: {e}"],
            )
