"""Prompt Versioning System for Dana voice agent.

Snapshots, hashes, validates, diffs, exports, and audits prompt versions.
"""

from __future__ import annotations

import hashlib
import json
import re
import difflib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from storage.repository import Repository


@dataclass
class PromptVersionSnapshotResult:
    """Result of snapshotting or creating a prompt version."""
    prompt_version_id: str
    prompt_name: str
    version: str
    source_file: str
    content_hash: str
    status: str
    created_by: str
    created_at: datetime
    changed_since_last_snapshot: bool
    previous_version_id: Optional[str] = None
    previous_content_hash: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class PromptVersionDiff:
    """Difference details between two prompt versions."""
    from_version_id: str
    to_version_id: str
    from_hash: str
    to_hash: str
    added_lines: int
    removed_lines: int
    changed_lines: int
    unified_diff: str
    summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


@dataclass
class PromptValidationResult:
    """Result of validation checks on prompt content."""
    passed: bool
    critical_failures: list[str] = field(default_factory=list)
    high_failures: list[str] = field(default_factory=list)
    medium_warnings: list[str] = field(default_factory=list)
    low_warnings: list[str] = field(default_factory=list)
    forbidden_phrases_found: list[str] = field(default_factory=list)
    required_sections_missing: list[str] = field(default_factory=list)
    word_count: int = 0
    line_count: int = 0
    content_hash: str = ""


@dataclass
class PromptVersionReport:
    """High-level report on all prompt versions for a given prompt name."""
    prompt_name: str
    total_versions: int
    latest_version_id: Optional[str] = None
    latest_hash: Optional[str] = None
    active_version_id: Optional[str] = None
    versions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PromptVersionManager:
    """Manages prompt snapshots, hashing, history, validation, and diffing."""

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.repository = repository or Repository()

    def compute_content_hash(self, content: str) -> str:
        """Compute stable SHA-256 hash of prompt content after normalizing line endings."""
        # Convert \r\n and \r to \n
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        # Strip trailing whitespace from each line
        lines = [line.rstrip() for line in normalized.split("\n")]
        normalized_content = "\n".join(lines)
        return hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()

    def load_prompt_file(self, path: str | Path) -> str:
        """Load prompt file content using UTF-8 encoding."""
        return Path(path).read_text(encoding="utf-8")

    def _translate_db_record(self, db_record: dict[str, Any]) -> dict[str, Any]:
        """Translate a database record format to the conceptual PromptVersion format."""
        qa = db_record.get("qa_thresholds") or {}
        if isinstance(qa, str):
            try:
                qa = json.loads(qa)
            except Exception:
                qa = {}

        created_at = db_record.get("created_at")
        if isinstance(created_at, str):
            from storage.repository import parse_dt
            created_at = parse_dt(created_at)

        approved_at = qa.get("approved_at")
        if isinstance(approved_at, str):
            from storage.repository import parse_dt
            approved_at = parse_dt(approved_at)

        return {
            "id": db_record["id"],
            "prompt_name": qa.get("prompt_name") or "unknown",
            "version": qa.get("version") or "unknown",
            "content": qa.get("content") or "",
            "content_hash": db_record.get("sha") or qa.get("content_hash") or "",
            "parent_version_id": qa.get("parent_version_id"),
            "source_file": db_record.get("file_path") or qa.get("source_file"),
            "source_file_hash": qa.get("source_file_hash") or db_record.get("sha") or "",
            "status": db_record.get("canary_status") or qa.get("status") or "snapshot",
            "created_by": db_record.get("created_by") or "unknown",
            "created_at": created_at,
            "approved_by": qa.get("approved_by"),
            "approved_at": approved_at,
            "notes": db_record.get("change_reason") or qa.get("notes"),
            "metadata": qa.get("metadata") or {}
        }

    async def get_prompt_version(self, version_id: str) -> dict[str, Any]:
        """Retrieve a prompt version by ID, returning a translated conceptual dictionary."""
        db_record = await self.repository.get_prompt_version(version_id)
        if not db_record:
            raise ValueError(f"PromptVersion not found: {version_id}")
        return self._translate_db_record(db_record)

    async def list_prompt_versions(self, prompt_name: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        """List tracked prompt versions, optionally filtered by prompt name."""
        db_records = await self.repository.query_prompt_versions({})
        translated = []
        for r in db_records:
            t = self._translate_db_record(r)
            if prompt_name is None or t["prompt_name"] == prompt_name:
                translated.append(t)

        def get_created_at(x):
            dt = x.get("created_at")
            if not dt:
                return datetime.min.replace(tzinfo=timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        translated.sort(key=get_created_at, reverse=True)
        return translated[:limit]

    async def get_latest_prompt_version(self, prompt_name: str) -> Optional[dict[str, Any]]:
        """Retrieve the latest prompt version for a given prompt name."""
        versions = await self.list_prompt_versions(prompt_name=prompt_name, limit=1)
        return versions[0] if versions else None

    async def create_prompt_version(
        self,
        prompt_name: str,
        content: str,
        created_by: str,
        version: Optional[str] = None,
        parent_version_id: Optional[str] = None,
        source_file: Optional[str] = None,
        status: str = "draft",
        notes: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PromptVersionSnapshotResult:
        """Programmatically create a new PromptVersion record."""
        import uuid
        content_hash = self.compute_content_hash(content)

        existing_versions = await self.list_prompt_versions(prompt_name)
        existing_v_strs = {ev.get("version") for ev in existing_versions}

        # Resolve version name
        if not version:
            today_str = datetime.now(timezone.utc).strftime("%Y.%m.%d")
            n = 1
            for ev in existing_versions:
                v_str = ev.get("version")
                if v_str and v_str.startswith(today_str):
                    try:
                        suffix = int(v_str.split(".")[-1])
                        if suffix >= n:
                            n = suffix + 1
                    except Exception:
                        pass
            version = f"{today_str}.{n}"
        else:
            if version in existing_v_strs:
                base = version
                suffix = 1
                if "." in version:
                    parts = version.split(".")
                    try:
                        suffix = int(parts[-1])
                        base = ".".join(parts[:-1])
                    except ValueError:
                        pass
                new_v = f"{base}.{suffix + 1}"
                while new_v in existing_v_strs:
                    suffix += 1
                    new_v = f"{base}.{suffix + 1}"
                version = new_v

        meta_payload = {
            "source_file": source_file,
            "source_file_hash": content_hash,
            "parent_version_id": parent_version_id,
            "notes": notes,
            "created_from": "prompt_versioning",
            "runtime_changed": False,
            "metadata": metadata or {}
        }

        db_record = {
            "id": str(uuid.uuid4()),
            "file_path": source_file or "",
            "sha": content_hash,
            "created_at": datetime.now(timezone.utc),
            "created_by": created_by,
            "change_reason": notes or "",
            "canary_status": status,
            "qa_thresholds": {
                "prompt_name": prompt_name,
                "version": version,
                "content": content,
                "parent_version_id": parent_version_id,
                "source_file": source_file,
                "source_file_hash": content_hash,
                "approved_by": None,
                "approved_at": None,
                "notes": notes,
                "metadata": meta_payload,
            }
        }

        # Save record
        saved_id = await self.repository.save_prompt_version(**db_record)

        return PromptVersionSnapshotResult(
            prompt_version_id=saved_id,
            prompt_name=prompt_name,
            version=version,
            source_file=source_file or "",
            content_hash=content_hash,
            status=status,
            created_by=created_by,
            created_at=db_record["created_at"],
            changed_since_last_snapshot=True,
            previous_version_id=parent_version_id,
            previous_content_hash=None,
            warnings=[]
        )

    async def snapshot_prompt_file(
        self,
        prompt_name: str,
        file_path: str | Path,
        created_by: str,
        version: Optional[str] = None,
        notes: Optional[str] = None,
        status: str = "snapshot",
    ) -> PromptVersionSnapshotResult:
        """Snapshot a prompt file, checking if changes were made since the last snapshot."""
        content = self.load_prompt_file(file_path)
        content_hash = self.compute_content_hash(content)

        latest = await self.get_latest_prompt_version(prompt_name)
        if latest and latest["content_hash"] == content_hash:
            # Deduplicate: return the latest snapshot ID and skip creating a duplicate
            return PromptVersionSnapshotResult(
                prompt_version_id=latest["id"],
                prompt_name=prompt_name,
                version=latest["version"],
                source_file=str(file_path),
                content_hash=latest["content_hash"],
                status=latest["status"],
                created_by=latest["created_by"],
                created_at=latest["created_at"],
                changed_since_last_snapshot=False,
                previous_version_id=latest.get("parent_version_id"),
                previous_content_hash=None,
                warnings=["No prompt changes detected; existing latest version reused."]
            )

        parent_version_id = latest["id"] if latest else None
        previous_content_hash = latest["content_hash"] if latest else None

        res = await self.create_prompt_version(
            prompt_name=prompt_name,
            content=content,
            created_by=created_by,
            version=version,
            parent_version_id=parent_version_id,
            source_file=str(file_path),
            status=status,
            notes=notes,
        )
        res.changed_since_last_snapshot = True
        res.previous_content_hash = previous_content_hash
        return res

    async def detect_prompt_drift(self, prompt_name: str, file_path: str | Path) -> dict[str, Any]:
        """Detect if the live prompt file on disk has changed compared to its latest recorded snapshot."""
        content = self.load_prompt_file(file_path)
        current_hash = self.compute_content_hash(content)

        latest = await self.get_latest_prompt_version(prompt_name)
        if not latest:
            return {
                "drift": True,
                "prompt_name": prompt_name,
                "file_path": str(file_path),
                "latest_version_id": None,
                "latest_version": None,
                "latest_hash": None,
                "current_hash": current_hash,
                "message": "No snapshots exist for this prompt."
            }

        drift = latest["content_hash"] != current_hash
        message = (
            f"Live prompt file has drift. Hash changed from {latest['content_hash']} to {current_hash}."
            if drift
            else "Live prompt file matches the latest snapshot."
        )

        return {
            "drift": drift,
            "prompt_name": prompt_name,
            "file_path": str(file_path),
            "latest_version_id": latest["id"],
            "latest_version": latest["version"],
            "latest_hash": latest["content_hash"],
            "current_hash": current_hash,
            "message": message
        }

    async def validate_prompt_content(self, content: str) -> PromptValidationResult:
        """Validate prompt content against deterministic safety and compliance rules."""
        critical_failures = []
        high_failures = []
        medium_warnings = []
        low_warnings = []
        forbidden_phrases_found = []
        required_sections_missing = []

        content_hash = self.compute_content_hash(content)
        word_count = len(content.split())
        line_count = len(content.splitlines())

        # Wording checklist for critical rules
        # If keyword matches are found without negation, flag as critical.
        negations = ["never", "do not", "don't", "prohibit", "forbidden", "not ", "no ", "can't", "cannot", "avoid", "must not"]

        def check_unnegated(pat: str) -> list[str]:
            matches = []
            for line in content.splitlines():
                line_lower = line.lower()
                if re.search(pat, line_lower):
                    if not any(neg in line_lower for neg in negations):
                        matches.append(line.strip())
            return matches

        # Critical checks
        # 1. Say "you qualify" or "they qualify"
        qualify_matches = check_unnegated(r"\bsay\s+['\"]?you\s+qualify\b|\btell\s+.*you\s+qualify\b|\bclaim\s+.*you\s+qualify\b")
        if qualify_matches:
            critical_failures.append("Prompt instructs agent to say 'you qualify' without negation.")
            forbidden_phrases_found.extend(qualify_matches)

        # 2. Approved / pre-approved / guaranteed approved
        approved_matches = check_unnegated(r"\bsay\s+['\"]?you\s+(?:are|'re)\s+approved\b|\btell\s+.*you\s+(?:are|'re)\s+approved\b|\bclaim\s+.*you\s+(?:are|'re)\s+approved\b|\bpromise\s+approval\b|\bguaranteed\s+approval\b")
        if approved_matches:
            critical_failures.append("Prompt instructs agent to say prospect is approved/pre-approved without negation.")
            forbidden_phrases_found.extend(approved_matches)

        # 3. Quote exact price, rate, premium
        price_matches = check_unnegated(r"\bexact\s+price\s+quote\b|\bquote\s+is\s+\$\s?\d+|\bexact\s+price\b|\bmonthly\s+cost\s+is\s+\$\s?\d+|\bpremium\s+is\s+\$\s?\d+")
        if price_matches:
            critical_failures.append("Prompt instructs agent to quote prices, rates, or premiums.")
            forbidden_phrases_found.extend(price_matches)

        # 4. Claim Dana is licensed
        licensed_matches = check_unnegated(r"\byou\s+are\s+(?:a\s+)?licensed\s+agent\b|\bi\s+am\s+(?:a\s+)?licensed\s+agent\b|\bi'm\s+(?:a\s+)?licensed\s+agent\b|\bclaim\s+(?:to\s+be\s+)?licensed\b")
        if licensed_matches:
            critical_failures.append("Prompt instructs agent to claim they are licensed.")
            forbidden_phrases_found.extend(licensed_matches)

        # 5. Claim Dana is human / real person
        human_matches = check_unnegated(r"\bpretend\s+you\s+are\s+(?:a\s+)?(?:real\s+person|human)\b|\bsay\s+you\s+are\s+(?:a\s+)?(?:real\s+person|human)\b|\bclaim\s+you\s+are\s+(?:a\s+)?(?:real\s+person|human)\b|\byou\s+are\s+a\s+real\s+person\b|\byou\s+are\s+a\s+human\b")
        if human_matches:
            critical_failures.append("Prompt instructs agent to claim they are human or a real person.")
            forbidden_phrases_found.extend(human_matches)

        # 6. Ask for sensitive information (SSN, Bank details, DOB etc.)
        sensitive_matches = check_unnegated(r"\b(?:ask\s+for|get|collect|request|obtain)\s+.*(?:social\s+security|ssn|bank|routing|credit\s+card|medicare|date\s+of\s+birth|dob)")
        if sensitive_matches:
            critical_failures.append("Prompt instructs agent to ask for sensitive information (SSN, Bank, Credit Card, DOB).")
            forbidden_phrases_found.extend(sensitive_matches)

        # 7. Transfer without explicit consent
        consent_matches = check_unnegated(r"transfer.*without.*(?:consent|asking)|auto-transfer|transfer\s+immediately")
        if consent_matches:
            critical_failures.append("Prompt instructs agent to transfer without explicit consent.")
            forbidden_phrases_found.extend(consent_matches)

        # 8. Ignore DNC
        dnc_matches = check_unnegated(r"ignore.*dnc|ignore.*do\s+not\s+call|bypass.*dnc")
        if dnc_matches:
            critical_failures.append("Prompt instructs agent to ignore do not call requests.")
            forbidden_phrases_found.extend(dnc_matches)

        # 9. Continue after wrong number
        wrong_num_matches = check_unnegated(r"continue.*wrong\s+number|ignore.*wrong\s+number")
        if wrong_num_matches:
            critical_failures.append("Prompt instructs agent to continue after a wrong number is identified.")
            forbidden_phrases_found.extend(wrong_num_matches)

        # 10. Bypass/override compliance filters
        compliance_matches = check_unnegated(r"bypass.*compliance|override.*safety|override.*compliance|ignore.*safety")
        if compliance_matches:
            critical_failures.append("Prompt instructs agent to bypass compliance filters or override safety rules.")
            forbidden_phrases_found.extend(compliance_matches)

        # High Failures
        # 1. encourages long multi-question responses
        mult_q_matches = check_unnegated(r"\b(?:ask|send|give)\s+multiple\s+questions\b|\bstack\s+questions\b|\blong\s+responses\b")
        if mult_q_matches:
            high_failures.append("Prompt encourages long multi-question responses or stacked questions.")
            forbidden_phrases_found.extend(mult_q_matches)

        # 2. encourages pressure after not interested
        pressure_matches = check_unnegated(r"\bpressure\b.*\bnot\s+interested\b|\bpush\b.*\bnot\s+interested\b|\bkeep\s+trying\b.*\bnot\s+interested\b")
        if pressure_matches:
            high_failures.append("Prompt encourages pressuring prospects who are not interested.")
            forbidden_phrases_found.extend(pressure_matches)

        # 3. says "only takes a minute"
        minute_matches = check_unnegated(r"\bonly\s+takes?\s+a\s+minute\b|\btakes?\s+a\s+minute\b")
        if minute_matches:
            high_failures.append("Prompt claims call only takes a minute.")
            forbidden_phrases_found.extend(minute_matches)

        # 4. claims government/state benefit in a misleading way
        gov_matches = check_unnegated(r"\bgovernment\s+benefit\b|\bstate\s+benefit\b|\bgovernment\s+program\b")
        if gov_matches:
            high_failures.append("Prompt references government or state benefits in a potentially misleading way.")
            forbidden_phrases_found.extend(gov_matches)

        # 5. tells Dana to hide that she is automated/AI in a deceptive way
        hide_matches = check_unnegated(r"\bhide\s+(?:ai|automation|automated)\b|\bdeceive\b")
        if hide_matches:
            high_failures.append("Prompt instructs agent to hide automated nature deceptively.")
            forbidden_phrases_found.extend(hide_matches)

        # 6. implies guaranteed acceptance without context
        guarantee_matches = check_unnegated(r"\bguaranteed\s+acceptance\b|\bguaranteed\s+coverage\b")
        if guarantee_matches:
            high_failures.append("Prompt implies guaranteed acceptance without context.")
            forbidden_phrases_found.extend(guarantee_matches)

        # 3. Medium warnings checks (completely missing rule concepts)
        lower_content = content.lower()

        # transfer requires explicit consent
        if not ("consent" in lower_content or "agree to transfer" in lower_content or "agree to the transfer" in lower_content):
            medium_warnings.append("Prompt lacks explicit transfer consent rule.")
            required_sections_missing.append("transfer requires explicit consent")

        # stop on DNC
        if not ("dnc" in lower_content or "do not call" in lower_content or "remove" in lower_content):
            medium_warnings.append("Prompt lacks explicit do not call (DNC) compliance rule.")
            required_sections_missing.append("stop on DNC")

        # stop on wrong number
        if not ("wrong number" in lower_content):
            medium_warnings.append("Prompt lacks explicit wrong number compliance rule.")
            required_sections_missing.append("stop on wrong number")

        # price-quote prohibition
        if not ("never" in lower_content and ("quote" in lower_content or "price" in lower_content or "premium" in lower_content)):
            medium_warnings.append("Prompt lacks price-quote prohibition rule.")
            required_sections_missing.append("do not quote prices")

        # licensing disclaimer
        if not ("not licensed" in lower_content or "not a licensed agent" in lower_content or "i'm not the licensed agent" in lower_content):
            medium_warnings.append("Prompt lacks licensing disclaimer rule.")
            required_sections_missing.append("do not claim licensed")

        # keep responses short
        if not ("short" in lower_content or "concise" in lower_content or "brief" in lower_content):
            medium_warnings.append("Prompt lacks short response guidance.")
            required_sections_missing.append("keep responses short")

        # ask one question at a time
        if not ("one question" in lower_content or "one at a time" in lower_content or "stack" in lower_content):
            medium_warnings.append("Prompt lacks one-question-at-a-time guidance.")
            required_sections_missing.append("ask one question at a time")

        passed = len(critical_failures) == 0 and len(high_failures) == 0

        return PromptValidationResult(
            passed=passed,
            critical_failures=critical_failures,
            high_failures=high_failures,
            medium_warnings=medium_warnings,
            low_warnings=low_warnings,
            forbidden_phrases_found=forbidden_phrases_found,
            required_sections_missing=required_sections_missing,
            word_count=word_count,
            line_count=line_count,
            content_hash=content_hash,
        )

    async def diff_prompt_versions(self, from_version_id: str, to_version_id: str) -> PromptVersionDiff:
        """Compare two prompt versions and generate diff and summary metrics."""
        from_ver = await self.get_prompt_version(from_version_id)
        to_ver = await self.get_prompt_version(to_version_id)

        from_lines = from_ver["content"].splitlines(keepends=True)
        to_lines = to_ver["content"].splitlines(keepends=True)

        diff = list(difflib.unified_diff(
            from_lines,
            to_lines,
            fromfile=f"version_{from_ver['version']}",
            tofile=f"version_{to_ver['version']}"
        ))

        unified_diff = "".join(diff)

        added = 0
        removed = 0
        for line in diff:
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1

        changed = min(added, removed)
        added_lines = added - changed
        removed_lines = removed - changed

        # Check safety-relevant changes
        keywords = [
            "transfer", "consent", "licensed", "price", "premium", "qualify", "approved",
            "dnc", "wrong number", "human", "real person", "ai", "bot", "ssn", "bank",
            "credit card", "medicare", "date of birth"
        ]
        safety_relevant_changes = []
        for line in diff:
            if (line.startswith("+") and not line.startswith("+++")) or (line.startswith("-") and not line.startswith("---")):
                line_lower = line.lower()
                for kw in keywords:
                    if kw in line_lower and kw not in safety_relevant_changes:
                        safety_relevant_changes.append(kw)

        summary = {
            "from_version": from_ver["version"],
            "to_version": to_ver["version"],
            "from_status": from_ver["status"],
            "to_status": to_ver["status"],
            "hash_changed": from_ver["content_hash"] != to_ver["content_hash"],
            "size_change_words": len(to_ver["content"].split()) - len(from_ver["content"].split()),
            "safety_relevant_changes": safety_relevant_changes
        }

        return PromptVersionDiff(
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            from_hash=from_ver["content_hash"],
            to_hash=to_ver["content_hash"],
            added_lines=added_lines,
            removed_lines=removed_lines,
            changed_lines=changed,
            unified_diff=unified_diff,
            summary=summary,
            warnings=[]
        )

    async def export_prompt_version(self, version_id: str, output_path: str | Path) -> str:
        """Export the prompt version content to a safe file path. Refuses to overwrite production prompts."""
        version = await self.get_prompt_version(version_id)
        out_path = Path(output_path)
        resolved_out = out_path.resolve()

        # Check if output_path is under prompts/ and exists
        prompts_dir = Path("prompts").resolve()
        if resolved_out.exists() and (prompts_dir in resolved_out.parents or resolved_out == prompts_dir / "final_expense_alex.md"):
            raise ValueError("Refusing to overwrite live production prompt files in prompts/ directory.")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(version["content"], encoding="utf-8")
        return str(out_path)

    async def generate_prompt_report(self, prompt_name: str, output_dir: str | Path = "data/prompt_versions") -> tuple[str, str]:
        """Generate high-level audit reports in JSON and Markdown formats."""
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        versions = await self.list_prompt_versions(prompt_name=prompt_name, limit=100)
        latest = versions[0] if versions else None
        total_versions = len(versions)

        # Drift check
        drift_status = None
        if latest and latest.get("source_file"):
            try:
                drift_status = await self.detect_prompt_drift(prompt_name, latest["source_file"])
            except Exception:
                pass

        # Validation check for latest version
        validation_summary = None
        if latest:
            validation_summary = await self.validate_prompt_content(latest["content"])

        # Compile JSON report
        report_data = {
            "prompt_name": prompt_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_versions": total_versions,
            "latest_version_id": latest["id"] if latest else None,
            "latest_hash": latest["content_hash"] if latest else None,
            "drift_status": drift_status,
            "validation_summary": {
                "passed": validation_summary.passed if validation_summary else None,
                "critical_failures": validation_summary.critical_failures if validation_summary else [],
                "high_failures": validation_summary.high_failures if validation_summary else [],
                "medium_warnings": validation_summary.medium_warnings if validation_summary else [],
                "forbidden_phrases_found": validation_summary.forbidden_phrases_found if validation_summary else [],
                "required_sections_missing": validation_summary.required_sections_missing if validation_summary else [],
                "word_count": validation_summary.word_count if validation_summary else 0,
                "line_count": validation_summary.line_count if validation_summary else 0,
            } if validation_summary else None,
            "versions": [
                {
                    "id": v["id"],
                    "version": v["version"],
                    "status": v["status"],
                    "content_hash": v["content_hash"],
                    "created_by": v["created_by"],
                    "created_at": v["created_at"].isoformat() if isinstance(v["created_at"], datetime) else str(v["created_at"]),
                    "parent_version": v["parent_version_id"]
                }
                for v in versions
            ]
        }

        json_path = out_dir / f"prompt_report_{prompt_name}.json"
        json_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

        # Compile Markdown report
        md_lines = [
            "# Dana Prompt Version Report",
            "",
            f"**Prompt:** {prompt_name}",
            f"**Generated at:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"**Total versions:** {total_versions}",
            f"**Latest version:** {latest['version'] if latest else 'None'} ({latest['id'] if latest else 'None'})",
            f"**Active registry version:** {latest['id'] if latest else 'None'}",
            "",
            "## Version History",
            "",
            "| version id | version | status | content hash | created by | created at | parent version |",
            "|---|---|---|---|---|---|---|",
        ]

        for v in versions:
            parent = v["parent_version_id"] or "None"
            created_at_str = v["created_at"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(v["created_at"], datetime) else str(v["created_at"])
            md_lines.append(
                f"| {v['id']} | {v['version']} | {v['status']} | `{v['content_hash'][:12]}` | {v['created_by']} | {created_at_str} | {parent} |"
            )

        md_lines.append("")
        md_lines.append("## Latest Validation Summary")
        if validation_summary:
            md_lines.extend([
                f"- **passed:** {'Yes' if validation_summary.passed else 'No'}",
                f"- **critical failures:** {len(validation_summary.critical_failures)}",
                f"- **high failures:** {len(validation_summary.high_failures)}",
                f"- **medium warnings:** {len(validation_summary.medium_warnings)}",
            ])
            if validation_summary.critical_failures:
                md_lines.append("\n### Critical Failures:")
                for cf in validation_summary.critical_failures:
                    md_lines.append(f"- {cf}")
            if validation_summary.high_failures:
                md_lines.append("\n### High Failures:")
                for hf in validation_summary.high_failures:
                    md_lines.append(f"- {hf}")
        else:
            md_lines.append("No validation data available.")

        md_lines.append("")
        md_lines.append("## Drift Status")
        if drift_status:
            md_lines.append(f"- **Drift detected:** {'Yes' if drift_status['drift'] else 'No'}")
            md_lines.append(f"- **Message:** {drift_status['message']}")
        else:
            md_lines.append("- No drift check performed (no source file registered).")

        # Safety changes check: look at diff between latest and previous if possible
        md_lines.append("")
        md_lines.append("## Safety-Relevant Notes")
        if len(versions) >= 2:
            try:
                diff_res = await self.diff_prompt_versions(versions[1]["id"], versions[0]["id"])
                kw_changes = diff_res.summary["safety_relevant_changes"]
                if kw_changes:
                    md_lines.append("Safety keywords modified in the latest version:")
                    for kw in kw_changes:
                        md_lines.append(f"- `{kw}`-related terms were modified.")
                else:
                    md_lines.append("- No safety-relevant keyword modifications detected in the latest version.")
            except Exception as e:
                md_lines.append(f"- Could not calculate recent safety diff: {e}")
        else:
            md_lines.append("- Insufficient version history to audit safety diffs.")

        md_lines.append("")
        md_lines.extend([
            "## Recommended Next Actions",
            "- snapshot if drift exists",
            "- run eval cases before patching",
            "- run transcript replay before patching",
            "- run simulations before patching",
            "- do not apply prompt patches automatically"
        ])

        md_path = out_dir / f"prompt_report_{prompt_name}.md"
        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        return str(json_path), str(md_path)
