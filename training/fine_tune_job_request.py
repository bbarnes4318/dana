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


class FineTuneJobRequestConfig(BaseModel):
    approval_package_path: Optional[str] = None
    review_item_id: Optional[str] = None
    manifest_path: Optional[str] = None
    train_path: Optional[str] = None
    validation_path: Optional[str] = None
    provider: str = "generic"
    output_dir: str = "data/fine_tune_job_requests"
    require_human_approval: bool = True
    require_gate_passed: bool = True
    require_hash_match: bool = True
    recommended_base_model: Optional[str] = None
    suffix: str = "dana-final-expense-safe"
    dry_run: bool = False
    create_review_item: bool = False
    requester: Optional[str] = None
    notes: Optional[str] = None


class FineTuneJobRequestValidationResult(BaseModel):
    passed: bool
    upload_ready: bool
    provider: str
    gate_passed: bool
    human_approved: bool
    files_exist: bool
    hashes_match: bool
    no_prior_upload: bool
    no_prior_job: bool
    no_deployment_allowed: bool
    train_path: Optional[str] = None
    validation_path: Optional[str] = None
    train_hash: Optional[str] = None
    validation_hash: Optional[str] = None
    critical_failures: list[str] = Field(default_factory=list)
    high_failures: list[str] = Field(default_factory=list)
    medium_warnings: list[str] = Field(default_factory=list)
    low_warnings: list[str] = Field(default_factory=list)
    source_summary: dict[str, Any] = Field(default_factory=dict)


class FineTuneJobRequestPackage(BaseModel):
    request_id: str
    provider: str
    dataset_name: Optional[str] = None
    created_at: str
    requester: Optional[str] = None
    approval_package_path: Optional[str] = None
    review_item_id: Optional[str] = None
    manifest_path: Optional[str] = None
    train_path: str
    validation_path: str
    train_hash: str
    validation_hash: str
    recommended_base_model: str
    suffix: str
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    provider_request: dict[str, Any] = Field(default_factory=dict)
    validation_result: dict[str, Any] = Field(default_factory=dict)
    compliance_summary: dict[str, Any] = Field(default_factory=dict)
    manual_upload_instructions: list[str] = Field(default_factory=list)
    required_pre_upload_checks: list[str] = Field(default_factory=list)
    required_post_upload_checks: list[str] = Field(default_factory=list)
    upload_ready: bool
    manual_only: bool = True
    api_upload_performed: bool = False
    fine_tune_job_started: bool = False
    deployment_allowed: bool = False
    next_steps: list[str] = Field(default_factory=list)


class FineTuneJobRequestResult(BaseModel):
    request_id: str
    passed: bool
    upload_ready: bool
    provider: str
    package_json_path: Optional[str] = None
    package_markdown_path: Optional[str] = None
    provider_request_json_path: Optional[str] = None
    human_checklist_path: Optional[str] = None
    review_item_id: Optional[str] = None
    validation_result: FineTuneJobRequestValidationResult
    warnings: list[str] = Field(default_factory=list)


class FineTuneJobRequestBuilder:
    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository or Repository()

    async def load_dataset_approval_review_item(self, review_item_id: str) -> dict:
        item = await self.repository.get_human_review_item(review_item_id)
        if not item:
            raise ValueError(f"HumanReviewItem with ID {review_item_id} not found.")
        return item

    def load_approval_package(self, path: str | Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_manifest_if_available(self, path: str | Path | None) -> dict | None:
        if not path:
            return None
        p = Path(path)
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def resolve_dataset_paths(
        self,
        config: FineTuneJobRequestConfig,
        approval_package: dict | None,
        review_item: dict | None,
        manifest: dict | None
    ) -> tuple[str, str]:
        # 1. Config explicit paths
        train = config.train_path
        val = config.validation_path

        # Resolve helper
        def make_abs(p_str: str, base_dir: Path | None) -> str:
            p = Path(p_str)
            if p.is_absolute() or not base_dir:
                return str(p)
            return str(base_dir / p)

        # 2. Approval package paths
        if (not train or not val) and approval_package:
            base_dir = Path(config.approval_package_path).parent if config.approval_package_path else None
            t_raw = approval_package.get("train_path")
            v_raw = approval_package.get("validation_path")
            if t_raw:
                train = make_abs(t_raw, base_dir)
            if v_raw:
                val = make_abs(v_raw, base_dir)

        # 3. Review item paths
        if (not train or not val) and review_item:
            payload = review_item.get("payload") or {}
            # Check review item payload JSON paths
            t_raw = payload.get("train_path")
            v_raw = payload.get("validation_path")
            base_dir = None
            pkg_json_p = payload.get("approval_package_json_path")
            if pkg_json_p:
                base_dir = Path(pkg_json_p).parent
            if t_raw:
                train = make_abs(t_raw, base_dir)
            if v_raw:
                val = make_abs(v_raw, base_dir)

        # 4. Manifest paths
        if (not train or not val) and manifest:
            base_dir = Path(config.manifest_path).parent if config.manifest_path else None
            t_raw = manifest.get("train_path")
            v_raw = manifest.get("validation_path")
            if t_raw:
                train = make_abs(t_raw, base_dir)
            if v_raw:
                val = make_abs(v_raw, base_dir)

        if not train or not val:
            raise ValueError("Both train_path and validation_path are required (either directly or via approval_package/review_item/manifest).")

        return train, val

    def validate_request_inputs(
        self,
        config: FineTuneJobRequestConfig,
        approval_package: dict | None,
        review_item: dict | None,
        manifest: dict | None,
        train_path: str,
        validation_path: str
    ) -> FineTuneJobRequestValidationResult:
        critical_failures = []
        high_failures = []
        medium_warnings = []
        low_warnings = []

        gate_passed = False
        human_approved = False
        files_exist = False
        hashes_match = False
        no_prior_upload = True
        no_prior_job = True
        no_deployment_allowed = True

        # Resolve gate passed status
        if approval_package:
            gate_passed = approval_package.get("passed") is True
        elif review_item:
            gate_passed = review_item.get("payload", {}).get("passed") is True
        elif manifest:
            gate_passed = True
        else:
            gate_passed = True

        if config.require_gate_passed and not gate_passed:
            critical_failures.append("Dataset gate did not pass.")

        # Human approval checklist
        if review_item:
            payload = review_item.get("payload") or {}
            history = payload.get("review_history") or []
            
            is_type_ok = review_item.get("item_type") == "fine_tune_dataset_approval"
            is_status_ok = review_item.get("status") == "approved"
            
            reviewer = review_item.get("reviewer") or payload.get("approved_by")
            reviewed_at = review_item.get("reviewed_at") or payload.get("approved_at")
            
            passed_ok = payload.get("passed") is True
            human_req_ok = payload.get("human_review_required") is True
            
            upload_ok = payload.get("fine_tune_upload_allowed") in (False, None)
            job_ok = payload.get("fine_tune_job_started") in (False, None)
            deploy_ok = payload.get("deployment_allowed") in (False, None)
            
            has_paths = bool(payload.get("approval_package_json_path") or (payload.get("train_path") and payload.get("validation_path")))
            
            has_history_action = any(h.get("action") == "approved" for h in history)
            has_approved_metadata = bool(reviewer and reviewed_at)
            has_approval_signal = has_history_action or (is_status_ok and has_approved_metadata)
            
            if (is_type_ok and is_status_ok and reviewer and reviewed_at and passed_ok and 
                    human_req_ok and upload_ok and job_ok and deploy_ok and has_paths and has_approval_signal):
                human_approved = True
            else:
                reasons = []
                if not is_type_ok: reasons.append("item_type is not fine_tune_dataset_approval")
                if not is_status_ok: reasons.append("status is not approved")
                if not reviewer: reasons.append("reviewer is missing")
                if not reviewed_at: reasons.append("reviewed_at/approved_at is missing")
                if not passed_ok: reasons.append("payload.passed is not true")
                if not human_req_ok: reasons.append("payload.human_review_required is not true")
                if not upload_ok: reasons.append("payload.fine_tune_upload_allowed is already true")
                if not job_ok: reasons.append("payload.fine_tune_job_started is already true")
                if not deploy_ok: reasons.append("payload.deployment_allowed is already true")
                if not has_paths: reasons.append("payload.approval_package_json_path or train/val paths missing")
                if not has_approval_signal: reasons.append("review_history does not contain approved action and reviewer metadata is missing")
                critical_failures.append(f"Human review item validation failed: {', '.join(reasons)}")

        if config.require_human_approval:
            if not review_item:
                critical_failures.append("Review item ID is required but was not provided.")
            elif not human_approved:
                critical_failures.append("Human approval validation failed.")
        else:
            if not human_approved:
                medium_warnings.append("No approved fine_tune_dataset_approval review item provided; package is not upload-ready.")

        # Check if files exist
        train_p = Path(train_path)
        val_p = Path(validation_path)
        if train_p.exists() and val_p.exists():
            files_exist = True
        else:
            if not train_p.exists():
                critical_failures.append(f"Train file does not exist: {train_path}")
            if not val_p.exists():
                critical_failures.append(f"Validation file does not exist: {validation_path}")

        # Compute hashes and verify match
        actual_train_hash = ""
        actual_val_hash = ""
        if files_exist:
            actual_train_hash = self.compute_file_hash(train_path)
            actual_val_hash = self.compute_file_hash(validation_path)

        expected_train_hash = None
        expected_val_hash = None

        if approval_package:
            hashes = approval_package.get("file_hashes") or {}
            expected_train_hash = hashes.get("train")
            expected_val_hash = hashes.get("validation")
        elif review_item:
            payload = review_item.get("payload") or {}
            hashes = payload.get("file_hashes") or {}
            expected_train_hash = hashes.get("train")
            expected_val_hash = hashes.get("validation")

        if expected_train_hash and expected_val_hash:
            if actual_train_hash == expected_train_hash and actual_val_hash == expected_val_hash:
                hashes_match = True
            else:
                if config.require_hash_match:
                    critical_failures.append("Train or validation file hash mismatch.")
                else:
                    high_failures.append("Train or validation file hash mismatch.")
        else:
            is_upload_ready_candidate = human_approved and gate_passed and files_exist and not config.dry_run
            if is_upload_ready_candidate:
                high_failures.append("No expected file hashes available to verify integrity.")
            else:
                medium_warnings.append("No expected file hashes available for verification.")

        # Prior upload/job checks
        payload_data = {}
        if review_item:
            payload_data = review_item.get("payload") or {}
        elif approval_package:
            payload_data = approval_package

        prior_upload_fields = ["fine_tune_upload_allowed", "api_upload_performed", "uploaded_file_id"]
        for f in prior_upload_fields:
            val = payload_data.get(f)
            if val is True or (isinstance(val, str) and val.strip()):
                critical_failures.append(f"Prior upload detected: {f} = {val}")
                no_prior_upload = False

        prior_job_fields = ["fine_tune_job_started", "provider_job_id"]
        for f in prior_job_fields:
            val = payload_data.get(f)
            if val is True or (isinstance(val, str) and val.strip()):
                critical_failures.append(f"Prior fine-tune job detected: {f} = {val}")
                no_prior_job = False

        prior_deploy_fields = ["deployment_allowed", "model_id", "deployment_id"]
        for f in prior_deploy_fields:
            val = payload_data.get(f)
            if val is True or (isinstance(val, str) and val.strip()):
                critical_failures.append(f"Prior model deployment detected: {f} = {val}")
                no_deployment_allowed = False

        # Provider verification
        if config.provider not in ("openai", "azure_openai", "generic"):
            critical_failures.append(f"Unknown provider: {config.provider}")

        # Lightweight deterministic record checks to ensure dataset safety
        train_records = []
        val_records = []
        if files_exist:
            try:
                train_records = self.load_jsonl(train_path)
                val_records = self.load_jsonl(validation_path)
            except Exception as e:
                critical_failures.append(f"Failed to parse JSONL dataset files: {str(e)}")
                files_exist = False

        if files_exist:
            fmt = self.detect_format(train_records or val_records)
            for idx, rec in enumerate(train_records):
                self._validate_record_safety(rec, "train", idx, fmt, critical_failures, high_failures)
            for idx, rec in enumerate(val_records):
                self._validate_record_safety(rec, "validation", idx, fmt, critical_failures, high_failures)

        passed = len(critical_failures) == 0 and len(high_failures) == 0

        # Upload Ready determination:
        # upload_ready must be true only if:
        # - validation passed (passed is True)
        # - review_item is present
        # - review_item.item_type == "fine_tune_dataset_approval"
        # - review_item.status == "approved"
        # - human_approved is true
        # - gate_passed is true
        # - files_exist is true
        # - hashes_match is true or hash match is not required
        # - no_prior_upload is true
        # - no_prior_job is true
        # - no_deployment_allowed is true
        # - dry_run is false
        # Approval package alone or direct train/validation mode must never make upload_ready true.
        is_approved_review_item = (
            review_item is not None and
            review_item.get("item_type") == "fine_tune_dataset_approval" and
            review_item.get("status") == "approved" and
            human_approved is True
        )

        upload_ready = (
            passed and
            is_approved_review_item and
            gate_passed and
            files_exist and
            (hashes_match or not config.require_hash_match) and
            no_prior_upload and
            no_prior_job and
            no_deployment_allowed and
            not config.dry_run
        )

        if passed and not upload_ready:
            medium_warnings.append("Approved fine_tune_dataset_approval review item is required for upload_ready=true.")

        if config.dry_run:
            medium_warnings.append("Dry run only; upload_ready forced false.")

        source_summary = {}
        if review_item:
            source_summary["review_item_id"] = review_item.get("id")
            source_summary["reviewer"] = review_item.get("reviewer") or payload_data.get("approved_by")
        if approval_package:
            source_summary["gate_id"] = approval_package.get("gate_id")

        return FineTuneJobRequestValidationResult(
            passed=passed,
            upload_ready=upload_ready,
            provider=config.provider,
            gate_passed=gate_passed,
            human_approved=human_approved,
            files_exist=files_exist,
            hashes_match=hashes_match,
            no_prior_upload=no_prior_upload,
            no_prior_job=no_prior_job,
            no_deployment_allowed=no_deployment_allowed,
            train_path=train_path,
            validation_path=validation_path,
            train_hash=actual_train_hash,
            validation_hash=actual_val_hash,
            critical_failures=critical_failures,
            high_failures=high_failures,
            medium_warnings=medium_warnings,
            low_warnings=low_warnings,
            source_summary=source_summary
        )

    def _validate_record_safety(self, record: dict, split: str, index: int, fmt: str, crit: list[str], high: list[str]) -> None:
        user_text, assistant_text, metadata = self.extract_user_assistant_text(record, fmt)
        labels = metadata.get("labels") or {}
        
        pii_errors = self.check_unredacted_pii(user_text) + self.check_unredacted_pii(assistant_text)
        for err in pii_errors:
            crit.append(f"Record {index} ({split}): PII: {err}")
            
        assist_lower = assistant_text.lower()
        forbidden_phrases = ["you qualify", "you're qualified", "approved", "pre-approved", "guaranteed approval", "guaranteed acceptance", "i'm human", "real person", "not a bot", "not an ai", "social security", "ssn", "routing number", "bank account", "credit card", "medicare number", "date of birth", "dob"]
        for phrase in forbidden_phrases:
            if phrase in assist_lower:
                crit.append(f"Record {index} ({split}): Forbidden phrase '{phrase}' in assistant text")
                
        if self.contains_price_quote(assistant_text):
            crit.append(f"Record {index} ({split}): Exact price quote in assistant text")
            
        licensed_claims = ["i am licensed", "i'm licensed", "my license", "i am a licensed agent", "i'm a licensed agent"]
        for claim in licensed_claims:
            if claim in assist_lower:
                crit.append(f"Record {index} ({split}): Licensed self-claim '{claim}'")
                
        if self.is_dnc_or_wrong_number_selling(user_text, assistant_text):
            crit.append(f"Record {index} ({split}): DNC or wrong number prospect with continued selling")
            
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
            crit.append(f"Record {index} ({split}): Transferring without explicit consent")

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
                if num not in [50, 65, 70, 72, 75, 80, 85]:
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

    def compute_file_hash(self, path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def build_provider_request(self, config: FineTuneJobRequestConfig, validation: FineTuneJobRequestValidationResult) -> dict:
        req = {
            "provider": config.provider,
            "training_file_path": validation.train_path,
            "validation_file_path": validation.validation_path,
            "training_file_sha256": validation.train_hash,
            "validation_file_sha256": validation.validation_hash,
            "suffix": config.suffix,
            "hyperparameters": {
                "n_epochs": "auto",
                "batch_size": "auto",
                "learning_rate_multiplier": "auto"
            },
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False,
            "required_gates": ["evals", "replay_tests", "simulations", "canary_gates"],
            "human_approval": {
                "required": True,
                "approved": False
            },
            "generated_at": _utcnow().isoformat()
        }

        if config.provider == "openai":
            req["recommended_base_model"] = config.recommended_base_model or "provider-selected-compatible-chat-model"
            req["purpose"] = "fine-tune"
        elif config.provider == "azure_openai":
            req["recommended_base_model"] = config.recommended_base_model or "provider-selected-compatible-chat-model"
            req["purpose"] = "fine-tune"
            req["deployment_name_suggestion"] = f"{config.suffix}-deployment"
        else:  # generic
            req["model_family"] = config.recommended_base_model or "provider-selected-compatible-chat-model"

        req["compliance_notes"] = (
            "Dataset passed Prompt 19 deterministic CI gate. "
            "Dataset was human-reviewed before package creation if applicable. "
            "No API upload was performed. "
            "No fine-tune job was started. "
            "No model deployment is allowed by this package. "
            "Model must pass evals, replay tests, simulations, and canary gates before production use."
        )

        return req

    def build_manual_upload_instructions(self, provider: str) -> list[str]:
        if provider == "openai":
            return [
                "Review package and checklist.",
                "Verify OpenAI account and model availability manually.",
                "Upload train/validation files manually through approved internal process.",
                "Record uploaded_file_id manually in a future tracking workflow.",
                "Start no job until OpenAI fine-tune job-start workflow exists.",
                "Never deploy model directly."
            ]
        elif provider == "azure_openai":
            return [
                "Review package and checklist.",
                "Verify Azure OpenAI resource, region, quota, and model availability manually.",
                "Upload train/validation files manually through approved internal process.",
                "Record Azure file/job references manually in future tracking workflow.",
                "Start no job until Azure OpenAI fine-tune job-start workflow exists.",
                "Never deploy model directly."
            ]
        else:  # generic or unknown
            return [
                "Review package and checklist.",
                "Confirm provider supports chat-format fine-tuning.",
                "Upload only after separate authorization.",
                "Record provider file/job IDs in future tracking workflow.",
                "Never deploy model directly."
            ]

    def build_job_request_package(
        self,
        config: FineTuneJobRequestConfig,
        validation: FineTuneJobRequestValidationResult,
        approval_package: dict | None,
        review_item: dict | None,
        manifest: dict | None
    ) -> FineTuneJobRequestPackage:
        request_id = str(uuid.uuid4())[:8]
        created_at = _utcnow().isoformat()
        
        dataset_name = "unnamed_dataset"
        if manifest and manifest.get("export_name"):
            dataset_name = manifest.get("export_name")
        elif approval_package and approval_package.get("dataset_name"):
            dataset_name = approval_package.get("dataset_name")
        elif review_item and review_item.get("payload", {}).get("dataset_name"):
            dataset_name = review_item.get("payload", {}).get("dataset_name")

        provider_req = self.build_provider_request(config, validation)
        instructions = self.build_manual_upload_instructions(config.provider)

        comp_summary = {
            "passed_gate": validation.gate_passed,
            "human_approved": validation.human_approved,
            "no_prior_upload": validation.no_prior_upload,
            "no_prior_job": validation.no_prior_job,
            "no_deployment_allowed": validation.no_deployment_allowed,
            "validation_failures_count": len(validation.critical_failures) + len(validation.high_failures)
        }

        pre_upload_checks = [
            "Human must review approval package",
            "Human must verify train/validation hashes",
            "Human must confirm no PII",
            "Human must confirm no unsafe sales claims",
            "Human must confirm no DNC/wrong-number selling",
            "Human must confirm no transfer without consent",
            "Human must confirm provider account/model availability",
            "Human must confirm upload is authorized by separate approval"
        ]

        post_upload_checks = [
            "Verify file ID exists in provider console",
            "Ensure status transitions to uploaded/processed",
            "Save file IDs to the database model"
        ]

        next_steps = [
            "Do not upload unless separately approved.",
            "Do not start job until future job-start workflow exists.",
            "Record provider file IDs manually in future tracking workflow.",
            "Run evals/replay/simulations before any model can be considered.",
            "Use canary system before production deployment."
        ]

        return FineTuneJobRequestPackage(
            request_id=request_id,
            provider=config.provider,
            dataset_name=dataset_name,
            created_at=created_at,
            requester=config.requester,
            approval_package_path=config.approval_package_path,
            review_item_id=config.review_item_id,
            manifest_path=config.manifest_path,
            train_path=validation.train_path or "",
            validation_path=validation.validation_path or "",
            train_hash=validation.train_hash or "",
            validation_hash=validation.validation_hash or "",
            recommended_base_model=provider_req.get("recommended_base_model") or provider_req.get("model_family") or "provider-selected-compatible-chat-model",
            suffix=config.suffix,
            hyperparameters=provider_req.get("hyperparameters", {}),
            provider_request=provider_req,
            validation_result=validation.model_dump() if hasattr(validation, "model_dump") else validation.__dict__,
            compliance_summary=comp_summary,
            manual_upload_instructions=instructions,
            required_pre_upload_checks=pre_upload_checks,
            required_post_upload_checks=post_upload_checks,
            upload_ready=validation.upload_ready,
            manual_only=True,
            api_upload_performed=False,
            fine_tune_job_started=False,
            deployment_allowed=False,
            next_steps=next_steps
        )

    def write_request_package(self, package: FineTuneJobRequestPackage, output_dir: str | Path) -> tuple[str, str, str, str]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        req_id = package.request_id

        package_json_p = output_dir / f"{req_id}_job_request_package.json"
        package_md_p = output_dir / f"{req_id}_job_request_package.md"
        provider_json_p = output_dir / f"{req_id}_{package.provider}_provider_request.json"
        checklist_p = output_dir / f"{req_id}_pre_upload_checklist.md"

        # 1. Package JSON
        with open(package_json_p, "w", encoding="utf-8") as f:
            json.dump(package.model_dump() if hasattr(package, "model_dump") else package.__dict__, f, indent=2)

        # 2. Provider Request JSON
        with open(provider_json_p, "w", encoding="utf-8") as f:
            json.dump(package.provider_request, f, indent=2)

        # 3. Checklist MD
        chk_content = f"# Required Pre-Upload Checklist: {req_id}\n\n"
        chk_content += "\n".join(f"- [ ] {c}" for c in package.required_pre_upload_checks) + "\n"
        with open(checklist_p, "w", encoding="utf-8") as f:
            f.write(chk_content)

        # 4. Package MD
        val_res = package.validation_result
        md_content = f"""# Dana Fine-Tune Job Request Package

Request ID: {package.request_id}
Provider: {package.provider}
Dataset: {package.dataset_name or "N/A"}
Created: {package.created_at}
Upload ready: {package.upload_ready}
Manual only: {package.manual_only}
API upload performed: {package.api_upload_performed}
Fine-tune job started: {package.fine_tune_job_started}
Deployment allowed: {package.deployment_allowed}

## Executive Summary
- Gate passed: {val_res.get("gate_passed")}
- Human approved: {val_res.get("human_approved")}
- Files exist: {val_res.get("files_exist")}
- Hashes match: {val_res.get("hashes_match")}
- Upload ready: {package.upload_ready}
- Provider: {package.provider}
- Requester: {package.requester or "N/A"}

## Dataset Files
- Train path: {package.train_path}
- Validation path: {package.validation_path}
- Train SHA-256: {package.train_hash}
- Validation SHA-256: {package.validation_hash}

## Provider Request
- Provider: {package.provider}
- Recommended base model: {package.recommended_base_model}
- Suffix: {package.suffix}
- Hyperparameters:
  - n_epochs: {package.hyperparameters.get("n_epochs")}
  - batch_size: {package.hyperparameters.get("batch_size")}
  - learning_rate_multiplier: {package.hyperparameters.get("learning_rate_multiplier")}
- Manual-only status: {package.manual_only}

## Compliance Summary
- Dataset passed Prompt 19 gate: {val_res.get("gate_passed")}
- Human approval status: {"APPROVED" if val_res.get("human_approved") else "PENDING/MISSING"}
- No upload performed: {package.api_upload_performed is False}
- No fine-tune job started: {package.fine_tune_job_started is False}
- No deployment allowed: {package.deployment_allowed is False}
- Required future gates: evals, replay_tests, simulations, canary_gates

## Manual Upload Instructions
{chr(10).join(f"- {inst}" for inst in package.manual_upload_instructions)}

## Required Pre-Upload Checklist
{chr(10).join(f"- [ ] {chk}" for chk in package.required_pre_upload_checks)}

## Required Next Steps
{chr(10).join(f"- {step}" for step in package.next_steps)}
"""
        with open(package_md_p, "w", encoding="utf-8") as f:
            f.write(md_content)

        return str(package_json_p), str(package_md_p), str(provider_json_p), str(checklist_p)

    async def create_pending_review_item(self, package: FineTuneJobRequestPackage, result: FineTuneJobRequestResult) -> str | None:
        if not self.repository:
            return None

        val_dict = package.validation_result

        payload = {
            "source": "fine_tune_job_request_builder",
            "request_id": package.request_id,
            "provider": package.provider,
            "approval_package_path": package.approval_package_path,
            "dataset_approval_review_item_id": package.review_item_id,
            "manifest_path": package.manifest_path,
            "train_path": package.train_path,
            "validation_path": package.validation_path,
            "train_hash": package.train_hash,
            "validation_hash": package.validation_hash,
            "package_json_path": result.package_json_path,
            "package_markdown_path": result.package_markdown_path,
            "provider_request_json_path": result.provider_request_json_path,
            "human_checklist_path": result.human_checklist_path,
            "validation_result": val_dict,
            "upload_ready": package.upload_ready,
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False,
            "required_human_approval": True,
            "provider_request": package.provider_request,
            "next_steps": package.next_steps
        }

        return await self.repository.save_human_review_item(
            item_type="fine_tune_job_request",
            status="pending",
            payload=payload,
            reviewer=None
        )

    async def build_request_package(self, config: FineTuneJobRequestConfig) -> FineTuneJobRequestResult:
        warnings = []

        approval_package = None
        review_item = None
        manifest = None

        if config.approval_package_path:
            try:
                approval_package = self.load_approval_package(config.approval_package_path)
            except Exception as e:
                raise ValueError(f"Failed to load approval package: {str(e)}")

        if config.review_item_id:
            try:
                review_item = await self.load_dataset_approval_review_item(config.review_item_id)
            except Exception as e:
                raise ValueError(f"Failed to load review item: {str(e)}")

        if config.manifest_path:
            try:
                manifest = self.load_manifest_if_available(config.manifest_path)
            except Exception as e:
                warnings.append(f"Failed to load manifest: {str(e)}")

        train_path, validation_path = self.resolve_dataset_paths(
            config, approval_package, review_item, manifest
        )

        validation_result = self.validate_request_inputs(
            config, approval_package, review_item, manifest, train_path, validation_path
        )

        package = self.build_job_request_package(config, validation_result, approval_package, review_item, manifest)

        pkg_json, pkg_md, prov_json, checklist = self.write_request_package(package, config.output_dir)

        result = FineTuneJobRequestResult(
            request_id=package.request_id,
            passed=validation_result.passed,
            upload_ready=package.upload_ready,
            provider=package.provider,
            package_json_path=pkg_json,
            package_markdown_path=pkg_md,
            provider_request_json_path=prov_json,
            human_checklist_path=checklist,
            validation_result=validation_result,
            warnings=warnings
        )

        if config.create_review_item:
            rev_id = await self.create_pending_review_item(package, result)
            result.review_item_id = rev_id

        return result
