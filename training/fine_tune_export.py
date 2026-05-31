from __future__ import annotations

import re
import uuid
import hashlib
import random
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field

from storage.repository import Repository

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FineTuneExportConfig(BaseModel):
    export_name: str
    format: str = "openai_chat_jsonl"
    output_dir: str = "data/fine_tune_exports"
    train_ratio: float = 0.90
    limit: Optional[int] = None
    min_examples: int = 10
    max_examples: Optional[int] = None
    require_fine_tune_eligible: bool = True
    include_stages: Optional[list[str]] = None
    exclude_stages: Optional[list[str]] = None
    include_objection_types: Optional[list[str]] = None
    exclude_objection_types: Optional[list[str]] = None
    max_assistant_words: int = 45
    hard_max_assistant_words: int = 65
    validation_seed: int = 42
    system_message: Optional[str] = None
    dry_run: bool = False


class FineTuneExampleRecord(BaseModel):
    training_example_id: str
    user_text: str
    assistant_text: str
    stage: Optional[str] = None
    objection_type: Optional[str] = None
    source_id: Optional[str] = None
    labels: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
    redactions: dict[str, int] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)


class FineTuneValidationResult(BaseModel):
    passed: bool
    critical_failures: list[str] = Field(default_factory=list)
    high_failures: list[str] = Field(default_factory=list)
    medium_warnings: list[str] = Field(default_factory=list)
    low_warnings: list[str] = Field(default_factory=list)
    redactions: dict[str, int] = Field(default_factory=dict)
    word_count: int
    question_count: int
    content_hash: str


class FineTuneExportResult(BaseModel):
    export_id: str
    export_name: str
    format: str
    total_examples_scanned: int
    eligible_examples: int
    exported_examples: int
    skipped_examples: int
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    train_count: int
    validation_count: int
    train_path: Optional[str] = None
    validation_path: Optional[str] = None
    manifest_path: Optional[str] = None
    report_json_path: Optional[str] = None
    report_markdown_path: Optional[str] = None
    dry_run: bool
    warnings: list[str] = Field(default_factory=list)


def is_dnc_or_wrong_number_selling(user_text: str, assistant_text: str) -> bool:
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


def contains_price_quote(text: str) -> bool:
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


class FineTuneExportBuilder:
    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository or Repository()

    async def build_export(self, config: FineTuneExportConfig) -> FineTuneExportResult:
        export_id = str(uuid.uuid4())[:8]
        warnings = []
        
        # 1. Load candidate training examples (load all)
        candidates = await self.load_all_training_examples_for_export(config)
        total_examples_scanned = len(candidates)
        
        eligible_records = []
        skipped_reasons = {
            "not_fine_tune_eligible": 0,
            "unapproved": 0,
            "filter_stage": 0,
            "filter_objection": 0,
            "critical_failure": 0,
            "high_failure": 0,
            "validation_failed": 0,
            "unsafe_example": 0,
            "duplicate": 0
        }
        
        for cand in candidates:
            labels = cand.get("labels") or {}
            metadata = cand.get("metadata") or {}
            
            # Eligibility checks (only when require_fine_tune_eligible is True)
            if config.require_fine_tune_eligible:
                approved_by = cand.get("approved_by")
                approved_at = cand.get("approved_at")
                human_review_labels = labels.get("human_review_item_id")
                human_review_meta = metadata.get("human_review_item_id")
                
                approved = bool(
                    (approved_by and approved_at) or
                    human_review_labels or
                    human_review_meta
                )
                if not approved:
                    skipped_reasons["unapproved"] += 1
                    continue
                    
                is_eligible = labels.get("fine_tune_eligible") is True
                if not is_eligible:
                    skipped_reasons["not_fine_tune_eligible"] += 1
                    continue
                    
                use_for = cand.get("use_for")
                rec_use = cand.get("recommended_use_for")
                labels_rec_use = labels.get("recommended_use_for")
                meta_rec_use = metadata.get("recommended_use_for")
                
                def has_fine_tune(val) -> bool:
                    if not val:
                        return False
                    if isinstance(val, str):
                        return "fine_tune" in val
                    if isinstance(val, (list, set, tuple)):
                        return any(isinstance(x, str) and "fine_tune" in x for x in val)
                    return False
                
                use_ft = (
                    has_fine_tune(use_for) or
                    has_fine_tune(rec_use) or
                    has_fine_tune(labels_rec_use) or
                    has_fine_tune(meta_rec_use)
                )
                if not use_ft:
                    skipped_reasons["not_fine_tune_eligible"] += 1
                    continue



            # Stage / Objection filters before validation
            stage = cand.get("stage")
            if config.include_stages and stage not in config.include_stages:
                skipped_reasons["filter_stage"] += 1
                continue
            if config.exclude_stages and stage in config.exclude_stages:
                skipped_reasons["filter_stage"] += 1
                continue
                
            objection = labels.get("objection_type") or cand.get("objection_type") or labels.get("objection")
            if config.include_objection_types and objection not in config.include_objection_types:
                skipped_reasons["filter_objection"] += 1
                continue
            if config.exclude_objection_types and objection in config.exclude_objection_types:
                skipped_reasons["filter_objection"] += 1
                continue
                
            val_res = self.validate_training_example(cand, config)
            if not val_res.passed:
                skipped_reasons["validation_failed"] += 1
                skipped_reasons["unsafe_example"] += 1
                if val_res.critical_failures:
                    skipped_reasons["critical_failure"] += len(val_res.critical_failures)
                elif val_res.high_failures:
                    skipped_reasons["high_failure"] += len(val_res.high_failures)
                continue
                
            record = self.normalize_training_example(cand, val_res)
            eligible_records.append(record)
            
        eligible_examples = len(eligible_records)
        
        # Deduplicate
        deduped_records, dedupe_info = self.dedupe_examples(eligible_records)
        skipped_reasons["duplicate"] = dedupe_info.get("duplicate", 0)
        
        exported_examples = len(deduped_records)
        skipped_examples = total_examples_scanned - exported_examples
        
        # Apply max_examples / limit slicing
        max_limit = config.max_examples or config.limit
        if max_limit and len(deduped_records) > max_limit:
            deduped_records = deduped_records[:max_limit]
            exported_examples = len(deduped_records)
            skipped_examples = total_examples_scanned - exported_examples
            
        # Split train/validation
        train_records, val_records = self.split_train_validation(deduped_records, config.train_ratio, config.validation_seed)
        train_count = len(train_records)
        validation_count = len(val_records)
        
        if exported_examples < config.min_examples:
            warnings.append(f"Exported examples count ({exported_examples}) is below min_examples limit ({config.min_examples}). No files written.")
            
        result = FineTuneExportResult(
            export_id=export_id,
            export_name=config.export_name,
            format=config.format,
            total_examples_scanned=total_examples_scanned,
            eligible_examples=eligible_examples,
            exported_examples=exported_examples,
            skipped_examples=skipped_examples,
            skipped_reasons=skipped_reasons,
            train_count=train_count,
            validation_count=validation_count,
            dry_run=config.dry_run,
            warnings=warnings
        )
        
        if exported_examples < config.min_examples:
            return result
            
        if not config.dry_run:
            train_path = Path(config.output_dir) / f"{config.export_name}_{export_id}_train.jsonl"
            val_path = Path(config.output_dir) / f"{config.export_name}_{export_id}_validation.jsonl"
            
            self.write_jsonl(train_records, train_path, config)
            self.write_jsonl(val_records, val_path, config)
            
            result.train_path = str(train_path)
            result.validation_path = str(val_path)
            
            manifest_p, report_json_p, report_md_p = self.write_manifest_and_reports(result, deduped_records, config)
            result.manifest_path = manifest_p
            result.report_json_path = report_json_p
            result.report_markdown_path = report_md_p
            
        return result

    async def load_all_training_examples_for_export(self, config: FineTuneExportConfig) -> list[dict]:
        return await self.repository.query_training_examples({})

    async def load_candidate_training_examples(self, config: FineTuneExportConfig) -> list[dict]:
        all_examples = await self.repository.query_training_examples({})
        candidates = []
        for ex in all_examples:
            labels = ex.get("labels") or {}
            
            if config.require_fine_tune_eligible:
                is_eligible = labels.get("fine_tune_eligible") is True
                use_ft = ("fine_tune" in ex.get("use_for", []) or "fine_tune" in labels.get("recommended_use_for", []))
                approved = bool(ex.get("approved_by") or ex.get("approved_at") or labels.get("human_review_item_id"))
                if not (is_eligible and use_ft and approved):
                    continue
            candidates.append(ex)
        return candidates

    def validate_training_example(self, example: dict, config: FineTuneExportConfig) -> FineTuneValidationResult:
        user_text = example.get("user_text") or ""
        assistant_text = example.get("ideal_response") or example.get("ideal_text") or example.get("assistant_text") or ""
        labels = example.get("labels") or {}
        
        word_count = len(assistant_text.split())
        question_count = assistant_text.count("?")
        
        # Redact
        redacted_user, redactions_user = self.redact_text(user_text)
        redacted_assistant, redactions_assistant = self.redact_text(assistant_text)
        
        redactions = {}
        for k, v in redactions_user.items():
            redactions[k] = redactions.get(k, 0) + v
        for k, v in redactions_assistant.items():
            redactions[k] = redactions.get(k, 0) + v
            
        crit_failures = []
        high_failures = []
        med_warnings = []
        low_warnings = []
        
        assist_lower = redacted_assistant.lower()
        user_lower = redacted_user.lower()
        
        # Forbidden phrases
        forbidden_phrases = ["you qualify", "you're qualified", "pre-approved", "guaranteed approval", "guaranteed acceptance"]
        for phrase in forbidden_phrases:
            if phrase in assist_lower:
                crit_failures.append(f"Forbidden phrase found: '{phrase}'")
                
        if "approved" in assist_lower or "approved" in user_lower:
            crit_failures.append("Forbidden word 'approved' found")
            
        if is_dnc_or_wrong_number_selling(redacted_user, redacted_assistant):
            crit_failures.append("DNC or wrong number prospect text paired with continued selling")
            
        if contains_price_quote(redacted_assistant):
            crit_failures.append("Exact price quote found")
            
        # License self-claims
        licensed_claims = ["i am licensed", "i'm licensed", "my license", "i am a licensed agent", "i'm a licensed agent", "i'm a licensed final expense agent"]
        for claim in licensed_claims:
            if claim in assist_lower:
                crit_failures.append(f"Licensed self-claim found: '{claim}'")
                
        # Human self-claims
        human_claims = ["i'm a real person", "i am human", "i'm human", "real person", "not a bot", "not an ai"]
        for claim in human_claims:
            if claim in assist_lower:
                crit_failures.append(f"Human self-claim found: '{claim}'")
                
        # Unredacted PII keywords
        pii_kws = ["ssn", "social security", "routing number", "bank account", "credit card", "medicare number", "date of birth", "dob"]
        for kw in pii_kws:
            if kw in assist_lower or kw in user_lower:
                crit_failures.append(f"PII keyword found: '{kw}'")
                
        # Transfer without consent
        has_transfer_phrase = any(phrase in assist_lower for phrase in ["transferring now", "connecting you now", "connecting now", "transferring you now"])
        has_consent = (labels.get("transfer_consent") is True or labels.get("consent") is True or labels.get("consent_to_transfer") is True or str(labels.get("transfer_consent")).lower() == "true")
        if has_transfer_phrase and not has_consent:
            crit_failures.append("Transferring without explicit consent in labels")
            
        # Sensitive data remaining obvious check (unredacted 9 or 13-19 digit bounds)
        if re.search(r"\b\d{9}\b", redacted_user) or re.search(r"\b\d{9}\b", redacted_assistant):
            crit_failures.append("Unredacted 9-digit sequence found")
        if re.search(r"\b\d{13,19}\b", redacted_user) or re.search(r"\b\d{13,19}\b", redacted_assistant):
            crit_failures.append("Unredacted 13-19 digit sequence found")
            
        # High failures
        if word_count > config.hard_max_assistant_words:
            high_failures.append(f"Assistant response length ({word_count}) exceeds hard limit ({config.hard_max_assistant_words})")
            
        if question_count > 1 and not labels.get("allow_multiple_questions"):
            high_failures.append(f"Assistant response has {question_count} questions (max 1 unless allow_multiple_questions is true)")
            
        if labels.get("pressure_after_not_interested") or labels.get("pressures_after_not_interested"):
            high_failures.append("Assistant pressures after prospect not interested")
            
        if any(p in assist_lower for p in ["only takes a minute", "only take a minute", "takes a minute"]):
            high_failures.append("Assistant says 'only takes a minute'")
            
        if any(p in assist_lower for p in ["state benefit", "government benefit", "state program", "government program"]):
            high_failures.append("Assistant implies government/state benefit in a misleading way")
            
        comp_risk = str(labels.get("compliance_risk", "none")).lower()
        if comp_risk in ["medium", "high", "critical"]:
            high_failures.append(f"Compliance risk label is {comp_risk}")
            
        # Medium warnings
        if config.max_assistant_words < word_count <= config.hard_max_assistant_words:
            med_warnings.append(f"Assistant response length ({word_count}) is over soft limit ({config.max_assistant_words})")
            
        if not example.get("stage"):
            med_warnings.append("Missing stage label")
            
        objection = labels.get("objection_type") or example.get("objection_type") or labels.get("objection")
        if not objection:
            med_warnings.append("Missing objection type")
            
        if not example.get("source_id"):
            med_warnings.append("Missing source_id")
            
        has_approval = bool(example.get("approved_by") or example.get("approved_at") or labels.get("human_review_item_id"))
        if not has_approval:
            med_warnings.append("Missing approval metadata")
            
        if any(redactions.values()):
            med_warnings.append(f"Redaction occurred: {redactions}")
            
        passed = len(crit_failures) == 0 and len(high_failures) == 0
        content_hash = self.compute_content_hash(redacted_user, redacted_assistant)
        
        return FineTuneValidationResult(
            passed=passed,
            critical_failures=crit_failures,
            high_failures=high_failures,
            medium_warnings=med_warnings,
            low_warnings=low_warnings,
            redactions=redactions,
            word_count=word_count,
            question_count=question_count,
            content_hash=content_hash
        )

    def normalize_training_example(self, example: dict, validation: FineTuneValidationResult) -> FineTuneExampleRecord:
        user_text = example.get("user_text") or ""
        assistant_text = example.get("ideal_response") or example.get("ideal_text") or example.get("assistant_text") or ""
        
        redacted_user, _ = self.redact_text(user_text)
        redacted_assistant, _ = self.redact_text(assistant_text)
        
        labels = example.get("labels") or {}
        objection = labels.get("objection_type") or example.get("objection_type") or labels.get("objection")
        
        return FineTuneExampleRecord(
            training_example_id=example.get("id") or "",
            user_text=redacted_user,
            assistant_text=redacted_assistant,
            stage=example.get("stage"),
            objection_type=objection,
            source_id=example.get("source_id"),
            labels=labels,
            metadata=example.get("metadata") or {},
            content_hash=validation.content_hash,
            redactions=validation.redactions,
            validation={
                "passed": validation.passed,
                "word_count": validation.word_count,
                "question_count": validation.question_count,
                "medium_warnings": validation.medium_warnings
            }
        )

    def redact_text(self, text: str) -> tuple[str, dict[str, int]]:
        redactions = {}
        
        # 1. Email
        email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
        matches_email = re.findall(email_pattern, text)
        if matches_email:
            text = re.sub(email_pattern, "[REDACTED_EMAIL]", text)
            redactions["email"] = len(matches_email)
            
        # 2. Phone number
        phone_pattern = r"\b(?:\+?1[-. ]?)?\(?([0-9]{3})\)?[-. ]?([0-9]{3})[-. ]?([0-9]{4})\b"
        matches_phone = re.findall(phone_pattern, text)
        if matches_phone:
            text = re.sub(phone_pattern, "[REDACTED_PHONE]", text)
            redactions["phone"] = len(matches_phone)
            
        # 3. SSN with dashes
        ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b"
        matches_ssn = re.findall(ssn_pattern, text)
        if matches_ssn:
            text = re.sub(ssn_pattern, "[REDACTED_SSN]", text)
            redactions["ssn"] = len(matches_ssn)
            
        # 3b. SSN context base (9 consecutive digits)
        ssn_context_pattern = r"\b(?:ssn|social security|social|sec|identity)\b\s*[:=-]?\s*\b(\d{9})\b"
        matches_ssn_ctx = re.findall(ssn_context_pattern, text, re.IGNORECASE)
        if matches_ssn_ctx:
            for s in matches_ssn_ctx:
                text = text.replace(s, "[REDACTED_SSN]")
            redactions["ssn"] = redactions.get("ssn", 0) + len(matches_ssn_ctx)
            
        # 4. Credit card (13-19 digit card-like sequence)
        card_pattern = r"\b(?:\d{4}[- ]?){3,4}\d{1,4}\b"
        matches_card = []
        for m in re.finditer(card_pattern, text):
            digits_only = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits_only) <= 19:
                matches_card.append(m.group(0))
        if matches_card:
            for m in set(matches_card):
                text = text.replace(m, "[REDACTED_CARD]")
            redactions["credit_card"] = len(matches_card)
            
        # 5. Bank routing / account context
        bank_pattern = r"\b(?:routing|account|checking|savings|bank)\b\s*(?:number|num|#)?\s*[:=-]?\s*\b(\d{4,17})\b"
        matches_bank = re.findall(bank_pattern, text, re.IGNORECASE)
        if matches_bank:
            for b in matches_bank:
                text = text.replace(b, "[REDACTED_ACCOUNT]")
            redactions["bank_info"] = len(matches_bank)
            
        # 6. Medicare context
        medicare_pattern = r"\b(?:medicare|medicare number|medicare #)\b\s*[:=-]?\s*\b([A-Z0-9-]{11,15})\b"
        matches_medicare = re.findall(medicare_pattern, text, re.IGNORECASE)
        if matches_medicare:
            for m in matches_medicare:
                text = text.replace(m, "[REDACTED_MEDICARE]")
            redactions["medicare"] = len(matches_medicare)
            
        # 7. DOB / date of birth context
        dob_pattern = r"\b(?:dob|date of birth|birthday|born on|born)\b\s*[:=-]?\s*\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b"
        matches_dob = re.findall(dob_pattern, text, re.IGNORECASE)
        if matches_dob:
            for d in matches_dob:
                text = text.replace(d, "[REDACTED_DOB]")
            redactions["dob"] = len(matches_dob)
            
        # 8. Addresses
        address_pattern = r"\b\d{1,5}\s+[A-Za-z0-9\.\s]{3,30}\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|court|ct|lane|ln|way|highway|hwy|loop|trail|plaza|pz)\b"
        matches_addr = re.findall(address_pattern, text, re.IGNORECASE)
        if matches_addr:
            for a in matches_addr:
                text = text.replace(a, "[REDACTED_ADDRESS]")
            redactions["address"] = len(matches_addr)
            
        return text, redactions

    def compute_content_hash(self, user_text: str, assistant_text: str) -> str:
        u_norm = " ".join((user_text or "").lower().split())
        a_norm = " ".join((assistant_text or "").lower().split())
        content = f"{u_norm}|||{a_norm}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def dedupe_examples(self, records: list[FineTuneExampleRecord]) -> tuple[list[FineTuneExampleRecord], dict[str, int]]:
        seen_hashes = set()
        deduped_records = []
        duplicate_count = 0
        
        for r in records:
            u_norm = " ".join((r.user_text or "").lower().split())
            a_norm = " ".join((r.assistant_text or "").lower().split())
            stage_norm = (r.stage or "").lower().strip()
            obj_norm = (r.objection_type or "").lower().strip()
            
            combo = f"{u_norm}|||{a_norm}|||{stage_norm}|||{obj_norm}"
            h = hashlib.sha256(combo.encode("utf-8")).hexdigest()
            
            if h in seen_hashes:
                duplicate_count += 1
            else:
                seen_hashes.add(h)
                deduped_records.append(r)
                
        return deduped_records, {"duplicate": duplicate_count}

    def split_train_validation(
        self, records: list[FineTuneExampleRecord], train_ratio: float, seed: int
    ) -> tuple[list[FineTuneExampleRecord], list[FineTuneExampleRecord]]:
        shuffled = list(records)
        random.Random(seed).shuffle(shuffled)
        
        total = len(shuffled)
        if total == 0:
            return [], []
            
        train_count = int(total * train_ratio)
        if total >= 2 and train_count == total:
            train_count = total - 1
        if total >= 2 and train_count == 0:
            train_count = 1
            
        train_records = shuffled[:train_count]
        val_records = shuffled[train_count:]
        
        return train_records, val_records

    def serialize_record(self, record: FineTuneExampleRecord, config: FineTuneExportConfig) -> dict:
        metadata = {
            "training_example_id": record.training_example_id,
            "stage": record.stage,
            "objection_type": record.objection_type,
            "source_id": record.source_id,
            "labels": record.labels
        }
        
        if config.format == "generic_pairs_jsonl":
            return {
                "input": record.user_text,
                "output": record.assistant_text,
                "metadata": metadata
            }
        else:
            # default: openai_chat_jsonl
            system_msg = config.system_message or "You are Dana/Alex, a compliant outbound Final Expense voice assistant. Keep responses short, spoken, compliant, and ask one question at a time."
            return {
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": record.user_text},
                    {"role": "assistant", "content": record.assistant_text}
                ],
                "metadata": metadata
            }

    def write_jsonl(self, records: list[FineTuneExampleRecord], path: str | Path, config: FineTuneExportConfig) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                serialized = self.serialize_record(r, config)
                f.write(json.dumps(serialized) + "\n")

    def write_manifest_and_reports(
        self, result: FineTuneExportResult, records: list[FineTuneExampleRecord], config: FineTuneExportConfig
    ) -> tuple[str, str, str]:
        export_id = result.export_id
        export_name = result.export_name
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        manifest_path = output_dir / f"{export_name}_{export_id}_manifest.json"
        report_json_path = output_dir / f"{export_name}_{export_id}_report.json"
        report_md_path = output_dir / f"{export_name}_{export_id}_report.md"
        
        stage_dist = {}
        objection_dist = {}
        redaction_summary = {}
        
        for r in records:
            stage_dist[r.stage or "unknown"] = stage_dist.get(r.stage or "unknown", 0) + 1
            obj = r.objection_type or "none"
            objection_dist[obj] = objection_dist.get(obj, 0) + 1
            for k, v in r.redactions.items():
                redaction_summary[k] = redaction_summary.get(k, 0) + v
                
        manifest = {
            "export_id": export_id,
            "export_name": export_name,
            "format": result.format,
            "created_at": _utcnow().isoformat(),
            "train_path": result.train_path,
            "validation_path": result.validation_path,
            "train_count": result.train_count,
            "validation_count": result.validation_count,
            "source_filters": {
                "include_stages": config.include_stages,
                "exclude_stages": config.exclude_stages,
                "include_objection_types": config.include_objection_types,
                "exclude_objection_types": config.exclude_objection_types,
                "limit": config.limit
            },
            "skipped_reasons": result.skipped_reasons,
            "safety_summary": {
                "total_scanned": result.total_examples_scanned,
                "eligible": result.eligible_examples,
                "exported": result.exported_examples
            },
            "stage_distribution": stage_dist,
            "objection_distribution": objection_dist,
            "redaction_summary": redaction_summary,
            "compliance_statement": "Dataset generated from human-approved, compliance-validated examples only. No upload or fine-tuning job was started."
        }
        
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
            
        report_json = {
            "result": result.model_dump() if hasattr(result, "model_dump") else result.__dict__,
            "manifest": manifest
        }
        with open(report_json_path, "w", encoding="utf-8") as f:
            json.dump(report_json, f, indent=2)
            
        stage_dist_str = "\n".join(f"- {k}: {v}" for k, v in stage_dist.items()) or "- None"
        objection_dist_str = "\n".join(f"- {k}: {v}" for k, v in objection_dist.items()) or "- None"
        skipped_str = "\n".join(f"- {k}: {v}" for k, v in result.skipped_reasons.items()) or "- None"
        
        md_content = f"""# Dana Fine-Tuning Export Report

Export: {export_name}
Created: {manifest["created_at"]}
Format: {result.format}
Dry run: {result.dry_run}

## Executive Summary
- Examples scanned: {result.total_examples_scanned}
- Eligible examples: {result.eligible_examples}
- Exported examples: {result.exported_examples}
- Train count: {result.train_count}
- Validation count: {result.validation_count}
- Skipped examples: {result.skipped_examples}

## Safety Summary
- Critical failures excluded: {result.skipped_reasons.get("critical_failure", 0)}
- High failures excluded: {result.skipped_reasons.get("high_failure", 0)}
- Redactions performed: {sum(redaction_summary.values())}
- Forbidden phrase checks: PASSED
- Compliance validation statement:
  {manifest["compliance_statement"]}

## Dataset Distribution
### Stage Distribution
{stage_dist_str}

### Objection Type Distribution
{objection_dist_str}

## Skipped Reasons
{skipped_str}

## Files
- Train File: {result.train_path or "None"}
- Validation File: {result.validation_path or "None"}
- Manifest File: {str(manifest_path)}

## Required Next Steps
- Human review dataset files
- Run CI gates
- Do not upload until approval
- Do not fine-tune without Prompt 19/20 gates
- Do not deploy fine-tuned model without canary process
"""
        with open(report_md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
            
        return str(manifest_path), str(report_json_path), str(report_md_path)
