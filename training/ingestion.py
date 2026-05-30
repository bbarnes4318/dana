"""Training Ingestion Service for Dana's continuous training system.

Handles content loading, normalization of speaker labels, sensitive data redaction,
and SHA-256-based deduplication of raw training materials.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel

from storage.repository import Repository

SPEAKER_MAP = {
    # Prospect aliases
    "prospect": "prospect",
    "customer": "prospect",
    "lead": "prospect",
    "user": "prospect",
    "caller": "prospect",
    # Agent aliases
    "agent": "agent",
    "dana": "agent",
    "alex": "agent",
    "assistant": "agent",
    "rep": "agent",
}

SPEAKER_LINE_PATTERN = re.compile(
    r"^\s*(prospect|customer|lead|user|caller|agent|dana|alex|assistant|rep)\s*:\s*(.*)$",
    re.IGNORECASE
)


class TrainingIngestionResult(BaseModel):
    """Result of ingesting a training source."""

    source_id: str
    source_type: str
    title: str
    content_hash: str
    status: str
    normalized_turn_count: int
    redaction_count: int
    duplicate_detected: bool
    warnings: list[str]


def normalize_speaker(speaker: Optional[str]) -> str:
    """Normalize messy speaker labels to prospect, agent, or unknown."""
    if not speaker:
        return "unknown"
    normalized = str(speaker).strip().lower()
    return SPEAKER_MAP.get(normalized, "unknown")


def normalize_turn_dict(turn_dict: dict, index: int) -> dict:
    """Extract and normalize fields from a turn dictionary."""
    speaker_val = None
    for key in ["speaker", "role", "name", "author"]:
        if key in turn_dict:
            speaker_val = turn_dict[key]
            break

    text_val = ""
    for key in ["text", "content", "message", "utterance"]:
        if key in turn_dict:
            text_val = turn_dict[key]
            break

    if not text_val and not speaker_val:
        text_val = str(turn_dict)

    timestamp_val = turn_dict.get("timestamp")

    return {
        "speaker": normalize_speaker(speaker_val),
        "text": str(text_val),
        "timestamp": timestamp_val,
        "turn_index": index,
    }


def parse_plain_text(text: str) -> list[dict]:
    """Parse plain text containing speaker-line formats (e.g., 'Agent: text')."""
    lines = text.splitlines()
    turns = []
    current_speaker = None
    current_text_parts = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        match = SPEAKER_LINE_PATTERN.match(line)
        if match:
            if current_speaker is not None or current_text_parts:
                turns.append({
                    "speaker": current_speaker or "unknown",
                    "text": " ".join(current_text_parts)
                })
            current_speaker = match.group(1)
            current_text_parts = [match.group(2).strip()]
        else:
            if current_speaker is not None or current_text_parts:
                current_text_parts.append(stripped)
            else:
                current_text_parts.append(stripped)

    if current_speaker is not None or current_text_parts:
        turns.append({
            "speaker": current_speaker or "unknown",
            "text": " ".join(current_text_parts)
        })

    normalized = []
    for idx, turn in enumerate(turns):
        normalized.append({
            "speaker": normalize_speaker(turn["speaker"]),
            "text": turn["text"],
            "turn_index": idx
        })
    return normalized


def normalize_turns(input_data: list | str) -> list[dict]:
    """Normalize raw input turns (list of dicts, list of strings, or a plain text string)."""
    if isinstance(input_data, str):
        lines = input_data.splitlines()
        has_speaker_lines = any(SPEAKER_LINE_PATTERN.match(line) for line in lines)
        if has_speaker_lines:
            return parse_plain_text(input_data)
        else:
            return [{
                "speaker": "unknown",
                "text": input_data.strip(),
                "turn_index": 0
            }]

    normalized = []
    current_index = 0
    for item in input_data:
        if isinstance(item, dict):
            normalized.append(normalize_turn_dict(item, current_index))
            current_index += 1
        elif isinstance(item, str):
            match = SPEAKER_LINE_PATTERN.match(item)
            if match:
                normalized.append({
                    "speaker": normalize_speaker(match.group(1)),
                    "text": match.group(2).strip(),
                    "turn_index": current_index
                })
            else:
                normalized.append({
                    "speaker": "unknown",
                    "text": item.strip(),
                    "turn_index": current_index
                })
            current_index += 1
    return normalized


def redact_text(text: str) -> tuple[str, int]:
    """Deterministic redaction of sensitive data (email, phone, SSN, cards, accounts)."""
    if not text:
        return "", 0

    count = 0

    # 1. Emails
    email_pattern = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b")
    text, matches = email_pattern.subn("[REDACTED_EMAIL]", text)
    count += matches

    # 2. SSNs (e.g. 123-45-6789 or 123 45 6789)
    ssn_pattern = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")
    text, matches = ssn_pattern.subn("[REDACTED_SSN]", text)
    count += matches

    # 3. Credit Cards: 13 to 19 digits (with optional spaces/dashes)
    card_pattern = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b|\b\d{13,19}\b")
    text, matches = card_pattern.subn("[REDACTED_CARD]", text)
    count += matches

    # 4. Phone Numbers (7-to-11 digit typical telephone formats)
    phone_pattern = re.compile(
        r"\b(?:\+?1[-. ]?)?\(?[0-9]{3}\)?[-. ]?[0-9]{3}[-. ]?[0-9]{4}\b|\b[0-9]{3}-[0-9]{4}\b"
    )
    text, matches = phone_pattern.subn("[REDACTED_PHONE]", text)
    count += matches

    # 5. Bank Routing Numbers (Obvious 9-digit numbers preceded by keywords)
    routing_pattern = re.compile(
        r"\b(?:routing|rtn|transit|bank|acct|account)\b\s*(?:number|no|#)?\s*(?::|-)?\s*\b(\d{9})\b",
        re.IGNORECASE
    )
    def routing_replacer(match):
        nonlocal count
        count += 1
        matched_str = match.group(0)
        num_str = match.group(1)
        return matched_str.replace(num_str, "[REDACTED_ACCOUNT]")
    text = routing_pattern.sub(routing_replacer, text)

    # 6. Long Numeric Identifiers (10+ digits sequence, or 9 digit account fallback)
    long_num_pattern = re.compile(r"\b\d[\d\s-]{8,25}\d\b")
    def long_num_replacer(match):
        nonlocal count
        digits_only = "".join(c for c in match.group(0) if c.isdigit())
        if len(digits_only) >= 9:
            count += 1
            return "[REDACTED_ACCOUNT]"
        return match.group(0)
    text = long_num_pattern.sub(long_num_replacer, text)

    return text, count


def extract_turns_from_json(data: Any) -> list[dict] | str:
    """Robust extractor for turns/content from a JSON object or list."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ["turns", "transcript"]:
            if key in data:
                val = data[key]
                if isinstance(val, (list, str)):
                    return val
        for key in ["content", "text"]:
            if key in data:
                val = data[key]
                if isinstance(val, str):
                    return val
    return str(data)


def extract_turns_from_jsonl(text: str) -> list[Any]:
    """Parse JSONL lines into individual dictionaries or strings."""
    turns = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            turns.append(json.loads(line))
        except json.JSONDecodeError:
            turns.append(line)
    return turns


class TrainingIngestionService:
    """Service class for ingesting training sources."""

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.repository = repository

    async def ingest_source(
        self,
        source_type: str,
        title: str,
        content: Optional[str] = None,
        file_path: Optional[str | Path] = None,
        source_uri: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TrainingIngestionResult:
        """Ingests raw training material from a string or file path."""
        # 1. Content loading
        if content is not None:
            raw_content = content
        elif file_path is not None:
            fpath = Path(file_path)
            if not fpath.exists():
                raise ValueError(f"File not found: {file_path}")
            ext = fpath.suffix.lower()
            if ext not in [".txt", ".md", ".json", ".jsonl"]:
                raise ValueError(f"Unsupported file format: {ext}")
            with open(fpath, "r", encoding="utf-8") as f:
                raw_content = f.read()
        else:
            raise ValueError("Either content or file_path must be provided.")

        # 2. Format parsing
        input_data = raw_content
        if file_path is not None:
            ext = Path(file_path).suffix.lower()
            if ext == ".json":
                try:
                    parsed = json.loads(raw_content)
                    extracted = extract_turns_from_json(parsed)
                    input_data = extracted
                except json.JSONDecodeError:
                    input_data = raw_content
            elif ext == ".jsonl":
                try:
                    input_data = extract_turns_from_jsonl(raw_content)
                except Exception:
                    input_data = raw_content
        else:
            stripped = raw_content.strip()
            if (stripped.startswith("[") and stripped.endswith("]")) or (
                stripped.startswith("{") and stripped.endswith("}")
            ):
                try:
                    parsed = json.loads(stripped)
                    input_data = extract_turns_from_json(parsed)
                except Exception:
                    input_data = raw_content
            else:
                lines = [l.strip() for l in stripped.splitlines() if l.strip()]
                if lines and all(l.startswith("{") and l.endswith("}") for l in lines):
                    try:
                        input_data = extract_turns_from_jsonl(raw_content)
                    except Exception:
                        input_data = raw_content

        # 3. Normalization & Redaction
        normalized_turns = normalize_turns(input_data)

        redaction_count = 0
        for turn in normalized_turns:
            redacted_text, count_in_turn = redact_text(turn["text"])
            turn["text"] = redacted_text
            redaction_count += count_in_turn

        # 4. Deduplication
        canonical_turns = json.dumps(normalized_turns, sort_keys=True)
        content_hash = hashlib.sha256(canonical_turns.encode("utf-8")).hexdigest()

        duplicate_detected = False
        source_id = None
        existing_status = "raw"

        if self.repository is not None:
            recent_sources = await self.repository.list_recent_training_sources(limit=1000)
            for src in recent_sources:
                meta = src.get("metadata") or {}
                if meta.get("content_hash") == content_hash:
                    duplicate_detected = True
                    source_id = src["id"]
                    existing_status = src["status"]
                    break

        if not duplicate_detected:
            metadata_dict = {
                "content_hash": content_hash,
                "normalized_turns": normalized_turns,
                "normalized_turn_count": len(normalized_turns),
                "redaction_count": redaction_count,
                "original_file_path": str(file_path) if file_path is not None else None,
                "original_metadata": metadata or {},
                "ingestion_version": "1.0.0",
            }

            final_uri = source_uri
            if not final_uri:
                if file_path is not None:
                    final_uri = f"file://{Path(file_path).absolute().as_posix()}"
                else:
                    final_uri = f"inline://{content_hash[:16]}"

            if self.repository is not None:
                source_id = await self.repository.save_training_source(
                    source_type=source_type,
                    source_uri=final_uri,
                    title=title,
                    status="raw",
                    metadata=metadata_dict,
                )
            else:
                source_id = f"mock-{content_hash[:12]}"

        return TrainingIngestionResult(
            source_id=source_id,
            source_type=source_type,
            title=title,
            content_hash=content_hash,
            status=existing_status,
            normalized_turn_count=len(normalized_turns),
            redaction_count=redaction_count,
            duplicate_detected=duplicate_detected,
            warnings=[],
        )
