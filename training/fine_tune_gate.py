from __future__ import annotations

import re
import uuid
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field

from storage.repository import Repository
from storage.schemas import HumanReviewItem

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FineTuneDatasetGateConfig(BaseModel):
    manifest_path: Optional[str] = None
    train_path: Optional[str] = None
    validation_path: Optional[str] = None
    output_dir: str = "data/fine_tune_approvals"
    expected_format: Optional[str] = None
    min_train_examples: int = 10
    min_validation_examples: int = 1
    max_assistant_words: int = 45
    hard_max_assistant_words: int = 65
    max_question_count: int = 1
    min_stage_coverage: int = 1
    min_objection_coverage: int = 1
    max_duplicate_rate: float = 0.02
    max_redaction_token_rate: float = 0.20
    require_training_example_ids: bool = True
    require_manifest: bool = False
    create_review_item: bool = False
    reviewer_request_name: Optional[str] = None
    fail_on_medium_warnings: bool = False


class FineTuneRecordCheck(BaseModel):
    record_index: int
    split: str  # "train" | "validation"
    record_id: Optional[str] = None
    passed: bool
    critical_failures: list[str] = Field(default_factory=list)
    high_failures: list[str] = Field(default_factory=list)
    medium_warnings: list[str] = Field(default_factory=list)
    low_warnings: list[str] = Field(default_factory=list)
    user_text_preview: str
    assistant_text_preview: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    word_count: int
    question_count: int
    content_hash: str


class FineTuneDatasetMetrics(BaseModel):
    train_count: int
    validation_count: int
    total_count: int
    stage_distribution: dict[str, int] = Field(default_factory=dict)
    objection_distribution: dict[str, int] = Field(default_factory=dict)
    source_distribution: dict[str, int] = Field(default_factory=dict)
    duplicate_count: int
    duplicate_rate: float
    redaction_token_count: int
    redaction_token_rate: float
    average_assistant_words: float
    max_assistant_words: int
    question_count_distribution: dict[int, int] = Field(default_factory=dict)
    format_detected: str
    metadata_coverage: dict[str, float] = Field(default_factory=dict)


class FineTuneDatasetGateResult(BaseModel):
    gate_id: str
    passed: bool
    manifest_path: Optional[str] = None
    train_path: str
    validation_path: str
    checked_at: str
    format: str
    metrics: FineTuneDatasetMetrics
    total_records_checked: int
    records_passed: int
    records_failed: int
    critical_failures: list[str] = Field(default_factory=list)
    high_failures: list[str] = Field(default_factory=list)
    medium_warnings: list[str] = Field(default_factory=list)
    low_warnings: list[str] = Field(default_factory=list)
    failed_records: list[FineTuneRecordCheck] = Field(default_factory=list)
    approval_package_json_path: Optional[str] = None
    approval_package_markdown_path: Optional[str] = None
    approval_checklist_path: Optional[str] = None
    review_item_id: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class FineTuneApprovalPackage(BaseModel):
    gate_id: str
    dataset_name: Optional[str] = None
    manifest_path: Optional[str] = None
    train_path: str
    validation_path: str
    passed: bool
    metrics: dict[str, Any] = Field(default_factory=dict)
    safety_summary: dict[str, Any] = Field(default_factory=dict)
    dataset_distribution: dict[str, Any] = Field(default_factory=dict)
    file_hashes: dict[str, str] = Field(default_factory=dict)
    human_review_required: bool = True
    recommended_decision: str  # "approve" | "reject" | "needs_changes"
    required_reviewer_checks: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    created_at: str


class FineTuneDatasetGate:
    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository or Repository()

    async def run_gate(self, config: FineTuneDatasetGateConfig) -> FineTuneDatasetGateResult:
        gate_id = str(uuid.uuid4())[:8]
        checked_at = _utcnow().isoformat()
        warnings = []
        critical_failures = []
        high_failures = []
        medium_warnings = []
        low_warnings = []

        manifest = None
        train_path_str = config.train_path
        val_path_str = config.validation_path

        # 1. Manifest verification if provided
        if config.manifest_path:
            try:
                manifest = self.load_manifest(config.manifest_path)
                manifest_dir = Path(config.manifest_path).parent
                
                m_train = manifest.get("train_path")
                m_val = manifest.get("validation_path")
                
                if m_train:
                    p = Path(m_train)
                    train_path_str = str(manifest_dir / p if not p.is_absolute() else p)
                if m_val:
                    p = Path(m_val)
                    val_path_str = str(manifest_dir / p if not p.is_absolute() else p)

                # Validate compliance_statement
                comp_stmt = manifest.get("compliance_statement")
                if not comp_stmt:
                    critical_failures.append("Compliance statement is missing in manifest JSON")
                elif "no upload" not in comp_stmt.lower() and "no fine-tune" not in comp_stmt.lower() and "no fine-tuning" not in comp_stmt.lower():
                    critical_failures.append("Compliance statement in manifest must state that no upload/fine-tune job was started")
                    
                # Validate safety_summary & skipped_reasons
                if "safety_summary" not in manifest:
                    warnings.append("Manifest safety_summary is missing")
                if "skipped_reasons" not in manifest:
                    warnings.append("Manifest skipped_reasons is missing")
                    
                # Report file validation
                rep_json = manifest.get("report_json_path")
                rep_md = manifest.get("report_markdown_path")
                if rep_json:
                    p = Path(rep_json)
                    p_res = manifest_dir / p if not p.is_absolute() else p
                    if not p_res.exists():
                        warnings.append(f"Referenced report JSON file does not exist: {rep_json}")
                if rep_md:
                    p = Path(rep_md)
                    p_res = manifest_dir / p if not p.is_absolute() else p
                    if not p_res.exists():
                        warnings.append(f"Referenced report markdown file does not exist: {rep_md}")
            except Exception as e:
                critical_failures.append(f"Failed to load or validate manifest: {str(e)}")
        elif config.require_manifest:
            critical_failures.append("Configuration requires a manifest file, but none was provided")
        else:
            warnings.append("No manifest provided; approval package is less complete.")

        if not train_path_str or not val_path_str:
            raise ValueError("Both train_path and validation_path are required (either directly or via manifest).")

        train_path = Path(train_path_str)
        val_path = Path(val_path_str)

        # File exist checks
        if not train_path.exists():
            critical_failures.append(f"Train file does not exist: {train_path_str}")
        if not val_path.exists():
            critical_failures.append(f"Validation file does not exist: {val_path_str}")

        # Parse JSONL records
        train_records = []
        validation_records = []
        
        if train_path.exists():
            try:
                train_records = self.load_jsonl(train_path)
            except Exception as e:
                critical_failures.append(f"Failed to parse train JSONL: {str(e)}")
                
        if val_path.exists():
            try:
                validation_records = self.load_jsonl(val_path)
            except Exception as e:
                critical_failures.append(f"Failed to parse validation JSONL: {str(e)}")

        format_detected = "openai_chat_jsonl"
        if train_records:
            format_detected = self.detect_format(train_records)
        elif validation_records:
            format_detected = self.detect_format(validation_records)

        if config.expected_format and format_detected != config.expected_format:
            high_failures.append(f"Expected format is '{config.expected_format}', but detected '{format_detected}'")

        # Validate records
        train_checks = []
        validation_checks = []
        
        for idx, rec in enumerate(train_records):
            train_checks.append(self.validate_record(rec, "train", idx, config, format_detected))
            
        for idx, rec in enumerate(validation_records):
            validation_checks.append(self.validate_record(rec, "validation", idx, config, format_detected))

        # Dataset-level validations
        metrics, ds_crit, ds_high, ds_med = self.validate_dataset_level(
            train_checks, validation_checks, config, manifest
        )
        metrics.format_detected = format_detected

        # Merge failures and warnings
        for rc in (train_checks + validation_checks):
            critical_failures.extend([f"Record {rc.record_index} ({rc.split}): {f}" for f in rc.critical_failures])
            high_failures.extend([f"Record {rc.record_index} ({rc.split}): {f}" for f in rc.high_failures])
            medium_warnings.extend([f"Record {rc.record_index} ({rc.split}): {f}" for f in rc.medium_warnings])
            low_warnings.extend([f"Record {rc.record_index} ({rc.split}): {f}" for f in rc.low_warnings])

        critical_failures.extend(ds_crit)
        high_failures.extend(ds_high)
        medium_warnings.extend(ds_med)

        failed_records = [rc for rc in (train_checks + validation_checks) if not rc.passed]
        total_records_checked = len(train_checks) + len(validation_checks)
        records_passed = sum(1 for rc in (train_checks + validation_checks) if rc.passed)
        records_failed = len(failed_records)

        passed = len(critical_failures) == 0 and len(high_failures) == 0
        if config.fail_on_medium_warnings and len(medium_warnings) > 0:
            passed = False

        result = FineTuneDatasetGateResult(
            gate_id=gate_id,
            passed=passed,
            manifest_path=config.manifest_path,
            train_path=str(train_path),
            validation_path=str(val_path),
            checked_at=checked_at,
            format=format_detected,
            metrics=metrics,
            total_records_checked=total_records_checked,
            records_passed=records_passed,
            records_failed=records_failed,
            critical_failures=critical_failures,
            high_failures=high_failures,
            medium_warnings=medium_warnings,
            low_warnings=low_warnings,
            failed_records=failed_records,
            warnings=warnings
        )

        # Generate approval package files
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        package = self.generate_approval_package(result, config, manifest)
        json_p, md_p, checklist_p = self.write_approval_package(package, result, output_dir)
        
        result.approval_package_json_path = json_p
        result.approval_package_markdown_path = md_p
        result.approval_checklist_path = checklist_p

        # Optional pending review item
        if config.create_review_item:
            review_id = await self.create_pending_review_item(package, result)
            result.review_item_id = review_id

        return result

    def load_manifest(self, path: str | Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_jsonl(self, path: str | Path) -> list[dict]:
        records = []
        with open(path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    records.append(json.loads(line_str))
                except Exception as e:
                    raise ValueError(f"Line {idx+1} is not valid JSON: {str(e)}")
        return records

    def detect_format(self, records: list[dict]) -> str:
        if not records:
            return "openai_chat_jsonl"
        first = records[0]
        if "messages" in first:
            return "openai_chat_jsonl"
        if "input" in first or "output" in first:
            return "generic_pairs_jsonl"
        return "openai_chat_jsonl"

    def extract_user_assistant_text(self, record: dict, format_name: str) -> tuple[str, str, dict]:
        metadata = record.get("metadata") or {}
        if format_name == "openai_chat_jsonl":
            messages = record.get("messages") or []
            user_text = ""
            assistant_text = ""
            for msg in messages:
                role = msg.get("role")
                content = msg.get("content") or ""
                if role == "user":
                    user_text = content
                elif role == "assistant":
                    assistant_text = content
            return user_text, assistant_text, metadata
        else:
            user_text = record.get("input") or ""
            assistant_text = record.get("output") or ""
            return user_text, assistant_text, metadata

    def check_unredacted_pii(self, text: str) -> list[str]:
        failures = []
        
        email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
        if re.search(email_pattern, text):
            failures.append("Unredacted email address found")
            
        phone_pattern = r"\b(?:\+?1[-. ]?)?\(?([0-9]{3})\)?[-. ]?([0-9]{3})[-. ]?([0-9]{4})\b"
        if re.search(phone_pattern, text):
            failures.append("Unredacted phone number found")
            
        ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b"
        if re.search(ssn_pattern, text):
            failures.append("Unredacted SSN found")
            
        ssn_context_pattern = r"\b(?:ssn|social security|social|sec|identity)\b\s*[:=-]?\s*\b(\d{9})\b"
        if re.search(ssn_context_pattern, text, re.IGNORECASE):
            failures.append("Unredacted SSN context pattern found")
            
        card_pattern = r"\b(?:\d{4}[- ]?){3,4}\d{1,4}\b"
        for m in re.finditer(card_pattern, text):
            digits_only = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits_only) <= 19:
                failures.append("Unredacted credit card pattern found")
                break
                
        bank_pattern = r"\b(?:routing|account|checking|savings|bank)\b\s*(?:number|num|#)?\s*[:=-]?\s*\b(\d{4,17})\b"
        if re.search(bank_pattern, text, re.IGNORECASE):
            failures.append("Unredacted bank routing/account context found")
            
        medicare_pattern = r"\b(?:medicare|medicare number|medicare #)\b\s*[:=-]?\s*\b([A-Z0-9-]{11,15})\b"
        if re.search(medicare_pattern, text, re.IGNORECASE):
            failures.append("Unredacted Medicare context found")
            
        dob_pattern = r"\b(?:dob|date of birth|birthday|born on|born)\b\s*[:=-]?\s*\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b"
        if re.search(dob_pattern, text, re.IGNORECASE):
            failures.append("Unredacted DOB context found")
            
        address_pattern = r"\b\d{1,5}\s+[A-Za-z0-9\.\s]{3,30}\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|court|ct|lane|ln|way|highway|hwy|loop|trail|plaza|pz)\b"
        if re.search(address_pattern, text, re.IGNORECASE):
            failures.append("Unredacted street address pattern found")
            
        # Obvious raw 9 or 13-19 digit bounds
        clean_text = text.replace("[REDACTED_SSN]", "").replace("[REDACTED_CARD]", "").replace("[REDACTED_PHONE]", "").replace("[REDACTED_ACCOUNT]", "").replace("[REDACTED_MEDICARE]", "")
        if re.search(r"\b\d{9}\b", clean_text):
            failures.append("Unredacted 9-digit sequence found")
        if re.search(r"\b\d{13,19}\b", clean_text):
            failures.append("Unredacted 13-19 digit sequence found")
            
        return failures

    def contains_price_quote(self, text: str) -> bool:
        if re.search(r"\$\d+", text):
            return True
        if re.search(r"\b\d+\s*(?:dollars|bucks)\b", text, re.IGNORECASE):
            return True
        if re.search(r"\b\d+\.\d{2}\b", text):
            return True
            
        price_terms = ["cost", "price", "premium", "rate", "monthly", "paying", "payment"]
        for word in price_terms:
            pattern = r"\b" + word + r"\b.*\b(\d+)\b"
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                num = int(match.group(1))
                if num not in [50, 65, 70, 72, 75, 80, 85]:  # allow typical ages
                    return True
            pattern_rev = r"\b(\d+)\b.*\b" + word + r"\b"
            match_rev = re.search(pattern_rev, text, re.IGNORECASE)
            if match_rev:
                num = int(match_rev.group(1))
                if num not in [50, 65, 70, 72, 75, 80, 85]:
                    return True
        return False

    def is_dnc_or_wrong_number_selling(self, user_text: str, assistant_text: str) -> bool:
        user_lower = user_text.lower()
        is_dnc = any(p in user_lower for p in ["do not call", "don't call", "stop calling", "remove me", "take me off", "remove my number"])
        is_wrong_num = any(p in user_lower for p in ["wrong number", "wrong person", "not the person", "no one here by that name", "not home"])
        
        if not (is_dnc or is_wrong_num):
            return False
            
        assist_lower = assistant_text.lower()
        selling_keywords = [
            "final expense", "coverage", "insurance", "benefit", "qualify",
            "beneficiary", "rate", "cost", "agent", "senior", "plan"
        ]
        has_selling_kw = any(kw in assist_lower for kw in selling_keywords)
        has_question = "?" in assistant_text
        
        return has_selling_kw or has_question

    def validate_record(self, record: dict, split: str, index: int, config: FineTuneDatasetGateConfig, format_name: str) -> FineTuneRecordCheck:
        critical_failures = []
        high_failures = []
        medium_warnings = []
        low_warnings = []
        
        user_text = ""
        assistant_text = ""
        metadata = {}
        
        if format_name == "openai_chat_jsonl":
            if "messages" not in record:
                critical_failures.append("Missing 'messages' list in record")
            else:
                messages = record.get("messages") or []
                has_sys = False
                has_user = False
                has_assist = False
                for msg in messages:
                    role = msg.get("role")
                    content = msg.get("content") or ""
                    if role == "system":
                        has_sys = True
                        if not content.strip():
                            medium_warnings.append("System message content is empty")
                        elif "dana" not in content.lower() and "alex" not in content.lower():
                            medium_warnings.append("System message seems unusual (doesn't mention Dana or Alex)")
                    elif role == "user":
                        has_user = True
                        if not content.strip():
                            critical_failures.append("User message content is empty")
                        user_text = content
                    elif role == "assistant":
                        has_assist = True
                        if not content.strip():
                            critical_failures.append("Assistant message content is empty")
                        assistant_text = content
                        
                if not has_sys:
                    critical_failures.append("System message is missing")
                if not has_user:
                    critical_failures.append("User message is missing")
                if not has_assist:
                    critical_failures.append("Assistant message is missing")
            metadata = record.get("metadata") or {}
        else:  # generic_pairs_jsonl
            if "input" not in record:
                critical_failures.append("Missing 'input' field in record")
            else:
                user_text = record.get("input") or ""
                if not user_text.strip():
                    critical_failures.append("Input text is empty")
            if "output" not in record:
                critical_failures.append("Missing 'output' field in record")
            else:
                assistant_text = record.get("output") or ""
                if not assistant_text.strip():
                    critical_failures.append("Output text is empty")
            metadata = record.get("metadata") or {}
            
        record_id = metadata.get("training_example_id")
        labels = metadata.get("labels") or {}
        
        word_count = len(assistant_text.split())
        question_count = assistant_text.count("?")
        
        if not critical_failures:
            # 2. Check unredacted PII
            user_pii = self.check_unredacted_pii(user_text)
            for f in user_pii:
                critical_failures.append(f"PII in user text: {f}")
                
            assist_pii = self.check_unredacted_pii(assistant_text)
            for f in assist_pii:
                critical_failures.append(f"PII in assistant text: {f}")
                
            # 3. Check forbidden words / phrases
            assist_lower = assistant_text.lower()
            forbidden_phrases = ["you qualify", "you're qualified", "approved", "pre-approved", "guaranteed approval", "guaranteed acceptance", "i'm human", "real person", "not a bot", "not an ai", "social security", "ssn", "routing number", "bank account", "credit card", "medicare number", "date of birth", "dob"]
            for phrase in forbidden_phrases:
                if phrase in assist_lower:
                    critical_failures.append(f"Forbidden phrase/word found in assistant text: '{phrase}'")
                    
            if self.contains_price_quote(assistant_text):
                critical_failures.append("Exact price quote found in assistant text")
                
            licensed_claims = ["i am licensed", "i'm licensed", "my license", "i am a licensed agent", "i'm a licensed agent"]
            for claim in licensed_claims:
                if claim in assist_lower:
                    critical_failures.append(f"Licensed self-claim found: '{claim}'")
                    
            if self.is_dnc_or_wrong_number_selling(user_text, assistant_text):
                critical_failures.append("DNC or wrong number prospect text paired with continued selling")
                
            has_transfer_phrase = any(phrase in assist_lower for phrase in ["transferring now", "connecting you now", "connecting now", "transferring you now"])
            has_consent = (
                metadata.get("transfer_consent") is True or
                labels.get("transfer_consent") is True or
                labels.get("consent") is True or
                labels.get("consent_to_transfer") is True or
                str(metadata.get("transfer_consent")).lower() == "true" or
                str(labels.get("transfer_consent")).lower() == "true"
            )
            if has_transfer_phrase and not has_consent:
                critical_failures.append("Transferring without explicit consent")
                
            # High Failures
            if word_count > config.hard_max_assistant_words:
                high_failures.append(f"Assistant response length ({word_count}) exceeds hard limit ({config.hard_max_assistant_words})")
                
            allow_multi = (
                metadata.get("allow_multiple_questions") is True or
                labels.get("allow_multiple_questions") is True or
                str(metadata.get("allow_multiple_questions")).lower() == "true" or
                str(labels.get("allow_multiple_questions")).lower() == "true"
            )
            if question_count > config.max_question_count and not allow_multi:
                high_failures.append(f"Assistant response has {question_count} questions (max {config.max_question_count} unless allow_multiple_questions is true)")
                
            pressure = (
                metadata.get("pressure_after_not_interested") is True or
                labels.get("pressure_after_not_interested") is True or
                metadata.get("pressures_after_not_interested") is True or
                labels.get("pressures_after_not_interested") is True or
                str(metadata.get("pressure_after_not_interested")).lower() == "true" or
                str(labels.get("pressure_after_not_interested")).lower() == "true"
            )
            if pressure:
                high_failures.append("Assistant pressures after prospect not interested")
                
            if any(p in assist_lower for p in ["only takes a minute", "only take a minute", "takes a minute"]):
                high_failures.append("Assistant says 'only takes a minute'")
                
            if any(p in assist_lower for p in ["state benefit", "government benefit", "state program", "government program"]):
                high_failures.append("Assistant implies government/state benefit in a misleading way")
                
            comp_risk_meta = str(metadata.get("compliance_risk", "none")).lower()
            comp_risk_labels = str(labels.get("compliance_risk", "none")).lower()
            if comp_risk_meta in ["medium", "high", "critical"] or comp_risk_labels in ["medium", "high", "critical"]:
                high_failures.append("Compliance risk label is medium/high/critical")
                
            # Medium Warnings
            if config.max_assistant_words < word_count <= config.hard_max_assistant_words:
                medium_warnings.append(f"Assistant response length ({word_count}) exceeds soft limit ({config.max_assistant_words})")
                
            if config.require_training_example_ids and not record_id:
                medium_warnings.append("Missing training_example_id")
                
            if not metadata.get("stage") and not labels.get("stage") and not record.get("stage"):
                medium_warnings.append("Missing stage label")
                
            if not metadata.get("objection_type") and not labels.get("objection_type") and not record.get("objection_type") and not labels.get("objection") and not metadata.get("objection"):
                medium_warnings.append("Missing objection type")
                
            if not metadata.get("source_id") and not labels.get("source_id") and not record.get("source_id"):
                medium_warnings.append("Missing source_id")
                
            redaction_count = (user_text.count("[REDACTED_") + assistant_text.count("[REDACTED_"))
            if redaction_count > 3:
                medium_warnings.append(f"Redaction tokens appear heavily ({redaction_count} occurrences)")
                
            if "labels" not in record and "labels" not in metadata:
                medium_warnings.append("Record metadata missing labels")

        passed = len(critical_failures) == 0 and len(high_failures) == 0
        if config.fail_on_medium_warnings and len(medium_warnings) > 0:
            passed = False
            
        content_hash = self.compute_content_hash(user_text, assistant_text)
        
        user_preview = user_text[:60] + "..." if len(user_text) > 60 else user_text
        assist_preview = assistant_text[:60] + "..." if len(assistant_text) > 60 else assistant_text
        
        return FineTuneRecordCheck(
            record_index=index,
            split=split,
            record_id=record_id,
            passed=passed,
            critical_failures=critical_failures,
            high_failures=high_failures,
            medium_warnings=medium_warnings,
            low_warnings=low_warnings,
            user_text_preview=user_preview,
            assistant_text_preview=assist_preview,
            metadata=metadata,
            word_count=word_count,
            question_count=question_count,
            content_hash=content_hash
        )

    def validate_dataset_level(
        self, train_checks: list[FineTuneRecordCheck], validation_checks: list[FineTuneRecordCheck], config: FineTuneDatasetGateConfig, manifest: dict | None
    ) -> tuple[FineTuneDatasetMetrics, list[str], list[str], list[str]]:
        critical_failures = []
        high_failures = []
        medium_warnings = []
        
        train_count = len(train_checks)
        validation_count = len(validation_checks)
        total_count = train_count + validation_count
        
        # A. Counts
        if train_count < config.min_train_examples:
            high_failures.append(f"Train count {train_count} is below minimum limit {config.min_train_examples}")
        if validation_count < config.min_validation_examples:
            high_failures.append(f"Validation count {validation_count} is below minimum limit {config.min_validation_examples}")
            
        if manifest:
            man_train = manifest.get("train_count")
            man_val = manifest.get("validation_count")
            if man_train is not None and man_train != train_count:
                high_failures.append(f"Manifest train_count {man_train} does not match actual train count {train_count}")
            if man_val is not None and man_val != validation_count:
                high_failures.append(f"Manifest validation_count {man_val} does not match actual validation count {validation_count}")
                
        # B. Duplicates
        train_hashes = {rc.content_hash for rc in train_checks}
        val_hashes = {rc.content_hash for rc in validation_checks}
        
        contamination = train_hashes.intersection(val_hashes)
        if contamination:
            high_failures.append(f"Train/validation split contamination detected: {len(contamination)} duplicate content hashes appear in both train and validation splits")
            
        all_checks = train_checks + validation_checks
        all_hashes = [rc.content_hash for rc in all_checks]
        unique_hashes = set(all_hashes)
        duplicate_count = len(all_hashes) - len(unique_hashes)
        duplicate_rate = duplicate_count / total_count if total_count > 0 else 0.0
        
        if duplicate_rate > config.max_duplicate_rate:
            high_failures.append(f"Duplicate rate {duplicate_rate:.4f} exceeds maximum threshold {config.max_duplicate_rate}")
            
        # C. Distribution
        stage_dist = {}
        objection_dist = {}
        source_dist = {}
        
        redaction_count = 0
        total_words = 0
        max_words = 0
        question_dist = {}
        
        id_covered = 0
        stage_covered = 0
        obj_covered = 0
        source_covered = 0
        labels_covered = 0
        ft_eligible_covered = 0
        
        for rc in all_checks:
            meta = rc.metadata
            labels = meta.get("labels") or {}
            
            stage_val = meta.get("stage") or labels.get("stage") or "unknown"
            stage_dist[stage_val] = stage_dist.get(stage_val, 0) + 1
            
            obj_val = meta.get("objection_type") or labels.get("objection_type") or labels.get("objection") or "none"
            objection_dist[obj_val] = objection_dist.get(obj_val, 0) + 1
            
            source_val = meta.get("source_id") or labels.get("source_id") or "unknown"
            source_dist[source_val] = source_dist.get(source_val, 0) + 1
            
            total_words += rc.word_count
            if rc.word_count > max_words:
                max_words = rc.word_count
            question_dist[rc.question_count] = question_dist.get(rc.question_count, 0) + 1
            
            has_redaction = (rc.user_text_preview.count("[REDACTED_") + rc.assistant_text_preview.count("[REDACTED_")) > 0
            if has_redaction:
                redaction_count += 1
                
            if rc.record_id:
                id_covered += 1
            if meta.get("stage") or labels.get("stage"):
                stage_covered += 1
            if meta.get("objection_type") or labels.get("objection_type") or labels.get("objection") or meta.get("objection"):
                obj_covered += 1
            if meta.get("source_id") or labels.get("source_id"):
                source_covered += 1
            if meta.get("labels") or labels:
                labels_covered += 1
            if labels.get("fine_tune_eligible") is True:
                ft_eligible_covered += 1
                
        if len(stage_dist) < config.min_stage_coverage:
            high_failures.append(f"Stage coverage {len(stage_dist)} is below minimum stage coverage limit {config.min_stage_coverage}")
            
        if len(objection_dist) < config.min_objection_coverage:
            high_failures.append(f"Objection coverage {len(objection_dist)} is below minimum objection coverage limit {config.min_objection_coverage}")
            
        if all(k in ["unknown", "none"] for k in stage_dist.keys()):
            medium_warnings.append("All records have unknown or none stage label")
            
        if len(objection_dist) <= 1:
            medium_warnings.append("Dataset only contains a single objection type (or none)")
            
        redaction_rate = redaction_count / total_count if total_count > 0 else 0.0
        if redaction_rate > config.max_redaction_token_rate:
            medium_warnings.append(f"Redaction token rate {redaction_rate:.4f} is high (exceeds {config.max_redaction_token_rate})")
            
        avg_words = total_words / total_count if total_count > 0 else 0.0
        
        metadata_coverage = {
            "training_example_id": id_covered / total_count if total_count > 0 else 0.0,
            "stage": stage_covered / total_count if total_count > 0 else 0.0,
            "objection_type": obj_covered / total_count if total_count > 0 else 0.0,
            "source_id": source_covered / total_count if total_count > 0 else 0.0,
            "labels": labels_covered / total_count if total_count > 0 else 0.0,
        }
        if ft_eligible_covered > 0:
            metadata_coverage["fine_tune_eligible"] = ft_eligible_covered / total_count if total_count > 0 else 0.0
            
        metrics = FineTuneDatasetMetrics(
            train_count=train_count,
            validation_count=validation_count,
            total_count=total_count,
            stage_distribution=stage_dist,
            objection_distribution=objection_dist,
            source_distribution=source_dist,
            duplicate_count=duplicate_count,
            duplicate_rate=duplicate_rate,
            redaction_token_count=redaction_count,
            redaction_token_rate=redaction_rate,
            average_assistant_words=avg_words,
            max_assistant_words=max_words,
            question_count_distribution=question_dist,
            format_detected="openai_chat_jsonl",
            metadata_coverage=metadata_coverage
        )
        
        return metrics, critical_failures, high_failures, medium_warnings

    def compute_file_hash(self, path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def compute_content_hash(self, user_text: str, assistant_text: str) -> str:
        u_norm = " ".join((user_text or "").lower().split())
        a_norm = " ".join((assistant_text or "").lower().split())
        content = f"{u_norm}|||{a_norm}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def generate_approval_package(self, result: FineTuneDatasetGateResult, config: FineTuneDatasetGateConfig, manifest: dict | None) -> FineTuneApprovalPackage:
        dataset_name = manifest.get("export_name") if manifest else "unnamed_dataset"
        
        file_hashes = {}
        if Path(result.train_path).exists():
            file_hashes["train"] = self.compute_file_hash(result.train_path)
        if Path(result.validation_path).exists():
            file_hashes["validation"] = self.compute_file_hash(result.validation_path)
            
        recommended_decision = "approve"
        if len(result.critical_failures) > 0:
            recommended_decision = "reject"
        elif len(result.high_failures) > 0 or len(result.medium_warnings) > 3 or (not result.passed):
            recommended_decision = "needs_changes"
            
        required_reviewer_checks = [
            "Review sample train records for speech quality",
            "Review sample validation records for speech quality",
            "Confirm no PII exists in any record",
            "Confirm no unsafe sales claims are made by the assistant",
            "Confirm no transfers without explicit consent are taught",
            "Confirm no DNC/wrong-number selling behaviors are present",
            "Confirm dataset purpose and scope",
            "Confirm this is only an export package, not an upload or fine-tune job"
        ]
        
        next_steps = []
        if recommended_decision == "approve":
            next_steps.extend([
                "If approved, proceed to Prompt 20 fine-tune job request package.",
                "Do not upload until human approval.",
                "Do not fine-tune until provider/job gate exists.",
                "Do not deploy model without eval, replay, simulation, and canary gates."
            ])
        elif recommended_decision == "needs_changes":
            next_steps.extend([
                "Address high failures and medium warnings listed in the executive summary.",
                "Re-run export builder with updated filters or clean the source training examples."
            ])
        else:
            next_steps.extend([
                "Reject dataset. Inspect critical failures and sanitize unsafe training examples in the database."
            ])
            
        return FineTuneApprovalPackage(
            gate_id=result.gate_id,
            dataset_name=dataset_name,
            manifest_path=config.manifest_path,
            train_path=result.train_path,
            validation_path=result.validation_path,
            passed=result.passed,
            metrics=result.metrics.model_dump() if hasattr(result.metrics, "model_dump") else result.metrics.__dict__,
            safety_summary={
                "critical_failures": len(result.critical_failures),
                "high_failures": len(result.high_failures),
                "medium_warnings": len(result.medium_warnings),
                "warnings": len(result.warnings)
            },
            dataset_distribution={
                "stage_distribution": result.metrics.stage_distribution,
                "objection_distribution": result.metrics.objection_distribution,
                "source_distribution": result.metrics.source_distribution
            },
            file_hashes=file_hashes,
            human_review_required=True,
            recommended_decision=recommended_decision,
            required_reviewer_checks=required_reviewer_checks,
            next_steps=next_steps,
            created_at=result.checked_at
        )

    def write_approval_package(self, package: FineTuneApprovalPackage, result: FineTuneDatasetGateResult, output_dir: str | Path) -> tuple[str, str, str]:
        output_dir = Path(output_dir)
        gate_id = result.gate_id
        
        json_path = output_dir / f"{gate_id}_approval_package.json"
        md_path = output_dir / f"{gate_id}_approval_package.md"
        checklist_path = output_dir / f"{gate_id}_human_checklist.md"
        
        # Write JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(package.model_dump() if hasattr(package, "model_dump") else package.__dict__, f, indent=2)
            
        # Write MD
        failed_records_rows = ""
        for fr in result.failed_records:
            failures_str = "<br>".join(fr.critical_failures + fr.high_failures + fr.medium_warnings)
            failed_records_rows += f"| {fr.split} | {fr.record_index} | {fr.record_id or 'N/A'} | {failures_str} | {fr.user_text_preview} | {fr.assistant_text_preview} |\n"
            
        if not failed_records_rows:
            failed_records_rows = "| None | | | | | |\n"
            
        stage_dist_str = "\n".join(f"- {k}: {v}" for k, v in result.metrics.stage_distribution.items())
        objection_dist_str = "\n".join(f"- {k}: {v}" for k, v in result.metrics.objection_distribution.items())
        source_dist_str = "\n".join(f"- {k}: {v}" for k, v in result.metrics.source_distribution.items())
        
        md_content = f"""# Dana Fine-Tune Dataset Approval Package

Gate ID: {result.gate_id}
Dataset: {package.dataset_name or "N/A"}
Generated: {result.checked_at}
Passed: {result.passed}
Recommended decision: {package.recommended_decision}

## Executive Summary
- Train records: {result.metrics.train_count}
- Validation records: {result.metrics.validation_count}
- Total records: {result.metrics.total_count}
- Critical failures: {len(result.critical_failures)}
- High failures: {len(result.high_failures)}
- Medium warnings: {len(result.medium_warnings)}
- Duplicate rate: {result.metrics.duplicate_rate:.4f}
- Redaction token rate: {result.metrics.redaction_token_rate:.4f}

## Dataset Files
- Manifest: {package.manifest_path or "None"}
- Train file: {package.train_path}
- Validation file: {package.validation_path}
- File hashes:
  - train: {package.file_hashes.get("train", "N/A")}
  - validation: {package.file_hashes.get("validation", "N/A")}

## Safety Review
- Forbidden phrase scan: {"FAILED" if any("forbidden" in f.lower() for f in result.critical_failures) else "PASSED"}
- PII scan: {"FAILED" if any("pii" in f.lower() for f in result.critical_failures) else "PASSED"}
- Price quote scan: {"FAILED" if any("price" in f.lower() for f in result.critical_failures) else "PASSED"}
- Licensing claim scan: {"FAILED" if any("licensed" in f.lower() for f in result.critical_failures) else "PASSED"}
- Human/AI claim scan: {"FAILED" if any("human" in f.lower() or "bot" in f.lower() or "ai" in f.lower() for f in result.critical_failures) else "PASSED"}
- Transfer consent scan: {"FAILED" if any("transfer" in f.lower() for f in result.critical_failures) else "PASSED"}
- DNC/wrong-number scan: {"FAILED" if any("dnc" in f.lower() or "wrong number" in f.lower() for f in result.critical_failures) else "PASSED"}

## Dataset Quality
### Stage Distribution
{stage_dist_str}

### Objection Type Distribution
{objection_dist_str}

### Source Distribution
{source_dist_str}

### Metadata Coverage
- training_example_id: {result.metrics.metadata_coverage.get("training_example_id", 0.0):.2f}
- stage: {result.metrics.metadata_coverage.get("stage", 0.0):.2f}
- objection_type: {result.metrics.metadata_coverage.get("objection_type", 0.0):.2f}
- source_id: {result.metrics.metadata_coverage.get("source_id", 0.0):.2f}
- labels: {result.metrics.metadata_coverage.get("labels", 0.0):.2f}

## Failed Records
| Split | Index | ID | Failures / Warnings | User Preview | Assistant Preview |
|---|---|---|---|---|---|
{failed_records_rows}
## Required Human Review
- Review sample train records
- Review sample validation records
- Confirm no PII
- Confirm no unsafe sales claims
- Confirm no transfer without consent
- Confirm no DNC/wrong-number selling
- Confirm dataset purpose and scope
- Confirm this is only an export package, not an upload or fine-tune job

## Required Next Steps
{chr(10).join(f"- {step}" for step in package.next_steps)}
"""
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
            
        # Write checklist
        chk_content = f"""# Human Approval Checklist: Fine-Tune Dataset Gate {gate_id}

{chr(10).join(f"- [ ] {check}" for check in package.required_reviewer_checks)}
"""
        with open(checklist_path, "w", encoding="utf-8") as f:
            f.write(chk_content)
            
        return str(json_path), str(md_path), str(checklist_path)

    async def create_pending_review_item(self, package: FineTuneApprovalPackage, result: FineTuneDatasetGateResult) -> str | None:
        if not self.repository:
            return None
            
        payload = {
            "source": "fine_tune_dataset_gate",
            "gate_id": package.gate_id,
            "manifest_path": package.manifest_path,
            "train_path": package.train_path,
            "validation_path": package.validation_path,
            "approval_package_json_path": result.approval_package_json_path,
            "approval_package_markdown_path": result.approval_package_markdown_path,
            "approval_checklist_path": result.approval_checklist_path,
            "passed": package.passed,
            "recommended_decision": package.recommended_decision,
            "metrics": package.metrics,
            "critical_failures": result.critical_failures,
            "high_failures": result.high_failures,
            "medium_warnings": result.medium_warnings,
            "file_hashes": package.file_hashes,
            "human_review_required": True,
            "fine_tune_upload_allowed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False
        }
        
        return await self.repository.save_human_review_item(
            item_type="fine_tune_dataset_approval",
            status="pending",
            payload=payload,
            reviewer=None
        )
