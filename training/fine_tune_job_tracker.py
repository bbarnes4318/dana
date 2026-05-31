from __future__ import annotations

import os
import sys
import json
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field

from storage.repository import Repository
from storage.schemas import HumanReviewItem

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FineTuneJobTrackerConfig(BaseModel):
    job_request_package_path: Optional[str] = None
    job_request_review_item_id: Optional[str] = None
    job_start_review_item_id: Optional[str] = None
    provider_file_id: Optional[str] = None
    provider_validation_file_id: Optional[str] = None
    provider_job_id: Optional[str] = None
    provider_model_id: Optional[str] = None
    provider: Optional[str] = None
    output_dir: str = "data/fine_tune_job_tracking"
    actor: Optional[str] = None
    reason: Optional[str] = None
    notes: Optional[str] = None
    create_review_item: bool = False
    dry_run: bool = False


class FineTuneJobStartEligibilityResult(BaseModel):
    eligible: bool
    upload_ready: bool
    provider: str
    request_id: Optional[str] = None
    job_request_review_item_id: Optional[str] = None
    job_start_review_item_id: Optional[str] = None
    package_path: Optional[str] = None
    train_path: Optional[str] = None
    validation_path: Optional[str] = None
    train_hash: Optional[str] = None
    validation_hash: Optional[str] = None
    files_exist: bool
    hashes_match: bool
    human_request_approved: bool
    human_start_approved: bool
    manual_only: bool
    api_upload_performed: bool
    fine_tune_job_started: bool
    deployment_allowed: bool
    critical_failures: list[str] = Field(default_factory=list)
    high_failures: list[str] = Field(default_factory=list)
    medium_warnings: list[str] = Field(default_factory=list)
    low_warnings: list[str] = Field(default_factory=list)
    source_summary: dict[str, Any] = Field(default_factory=dict)


class FineTuneJobTrackingRecord(BaseModel):
    tracking_id: str
    request_id: str
    provider: str
    status: str
    created_at: str
    updated_at: str
    actor: Optional[str] = None
    job_request_package_path: Optional[str] = None
    job_request_review_item_id: Optional[str] = None
    job_start_review_item_id: Optional[str] = None
    train_path: Optional[str] = None
    validation_path: Optional[str] = None
    train_hash: Optional[str] = None
    validation_hash: Optional[str] = None
    provider_file_id: Optional[str] = None
    provider_validation_file_id: Optional[str] = None
    provider_job_id: Optional[str] = None
    provider_model_id: Optional[str] = None
    manual_only: bool
    api_upload_performed: bool
    fine_tune_job_started: bool
    deployment_allowed: bool
    upload_ready: bool = False
    start_authorized: bool
    audit_history: list[dict[str, Any]] = Field(default_factory=list)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FineTuneJobTrackingResult(BaseModel):
    tracking_id: Optional[str] = None
    request_id: Optional[str] = None
    operation: str
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
    success: bool
    message: str
    eligibility: Optional[FineTuneJobStartEligibilityResult] = None
    record: Optional[FineTuneJobTrackingRecord] = None
    review_item_id: Optional[str] = None
    report_json_path: Optional[str] = None
    report_markdown_path: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class FineTuneJobTracker:
    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository or Repository()

    def load_job_request_package(self, path: str | Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    async def load_human_review_item(self, item_id: str) -> dict:
        item = await self.repository.get_human_review_item(item_id)
        if not item:
            raise ValueError(f"HumanReviewItem with ID {item_id} not found.")
        return item

    def compute_file_hash(self, path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def validate_status_transition(self, previous_status: str, new_status: str) -> None:
        allowed = {
            "requested": {"start_approval_pending"},
            "start_approval_pending": {"start_approved"},
            "start_approved": {"files_uploaded_manual", "blocked", "job_started_manual"}, # allow job_started_manual under override transitions
            "files_uploaded_manual": {"job_started_manual", "blocked"},
            "job_started_manual": {"running_manual", "blocked"},
            "running_manual": {"succeeded_manual", "failed_manual", "cancelled_manual", "blocked"},
            "blocked": {"archived"},
            "succeeded_manual": {"archived"},
            "failed_manual": {"archived"},
            "cancelled_manual": {"archived"}
        }
        
        if previous_status not in allowed or new_status not in allowed[previous_status]:
            raise ValueError(f"Status transition from '{previous_status}' to '{new_status}' is disallowed.")

    def append_audit(
        self,
        history: list[dict[str, Any]],
        operation: str,
        actor: str,
        reason: str,
        previous_status: str | None,
        new_status: str
    ) -> list[dict[str, Any]]:
        updated = list(history)
        updated.append({
            "operation": operation,
            "actor": actor,
            "reason": reason,
            "previous_status": previous_status,
            "new_status": new_status,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        return updated

    async def check_start_eligibility(self, config: FineTuneJobTrackerConfig) -> FineTuneJobStartEligibilityResult:
        critical_failures = []
        high_failures = []
        medium_warnings = []
        low_warnings = []
        
        provider = config.provider
        request_id = None
        job_request_review_item_id = config.job_request_review_item_id
        job_start_review_item_id = config.job_start_review_item_id
        package_path = config.job_request_package_path
        
        train_path = None
        validation_path = None
        train_hash = None
        validation_hash = None
        
        files_exist = False
        hashes_match = False
        human_request_approved = False
        human_start_approved = False
        manual_only = True
        api_upload_performed = False
        fine_tune_job_started = False
        deployment_allowed = False
        
        # Verify provider
        if provider and provider not in ("openai", "azure_openai", "generic"):
            critical_failures.append(f"Invalid provider: {provider}")

        # A. Prompt 20 package
        package = None
        if package_path:
            p_path = Path(package_path)
            if not p_path.exists():
                critical_failures.append(f"Job request package file does not exist: {package_path}")
            else:
                try:
                    package = self.load_job_request_package(package_path)
                except Exception as e:
                    critical_failures.append(f"Failed to parse job request package JSON: {str(e)}")
                    
            if package:
                request_id = package.get("request_id")
                pkg_provider = package.get("provider")
                if pkg_provider:
                    if pkg_provider not in ("openai", "azure_openai", "generic"):
                        critical_failures.append(f"Invalid package provider: {pkg_provider}")
                    if provider and pkg_provider != provider:
                        critical_failures.append(f"Provider mismatch: package says '{pkg_provider}', config says '{provider}'")
                    provider = pkg_provider
                
                val_result = package.get("validation_result") or {}
                pkg_passed = package.get("passed") is True or val_result.get("passed") is True
                if not pkg_passed:
                    critical_failures.append("Job request package did not pass validation.")
                
                if package.get("upload_ready") is not True:
                    critical_failures.append("Job request package is not upload_ready.")
                if package.get("manual_only") is not True:
                    critical_failures.append("Job request package manual_only is not true.")
                if package.get("api_upload_performed") is True:
                    critical_failures.append("Prior API upload detected in package.")
                    api_upload_performed = True
                if package.get("fine_tune_job_started") is True:
                    critical_failures.append("Prior fine-tune job detected in package.")
                    fine_tune_job_started = True
                if package.get("deployment_allowed") is True:
                    critical_failures.append("Prior deployment allowed detected in package.")
                    deployment_allowed = True
                    
                prov_req = package.get("provider_request") or {}
                if not prov_req:
                    critical_failures.append("Provider request missing in package.")
                else:
                    if prov_req.get("manual_only") is not True:
                        critical_failures.append("Provider request manual_only is not true.")
                    if prov_req.get("api_upload_performed") is True:
                        critical_failures.append("Prior API upload detected in provider request.")
                        api_upload_performed = True
                    if prov_req.get("fine_tune_job_started") is True:
                        critical_failures.append("Prior fine-tune job detected in provider request.")
                        fine_tune_job_started = True
                    if prov_req.get("deployment_allowed") is True:
                        critical_failures.append("Prior deployment allowed detected in provider request.")
                        deployment_allowed = True

                train_path = train_path or package.get("train_path")
                validation_path = validation_path or package.get("validation_path")
                train_hash = train_hash or package.get("train_hash")
                validation_hash = validation_hash or package.get("validation_hash")

        # B. Prompt 20 job request review item
        req_review_item = None
        if job_request_review_item_id:
            try:
                req_review_item = await self.load_human_review_item(job_request_review_item_id)
            except Exception as e:
                critical_failures.append(f"Failed to load job request review item: {str(e)}")
                
            if req_review_item:
                if req_review_item.get("item_type") != "fine_tune_job_request":
                    critical_failures.append("Review item is not of type 'fine_tune_job_request'.")
                
                status_ok = req_review_item.get("status") == "approved"
                reviewer = req_review_item.get("reviewer")
                reviewed_at = req_review_item.get("reviewed_at")
                
                payload = req_review_item.get("payload") or {}
                history = payload.get("review_history") or []
                has_history_action = any(h.get("action") == "approved" for h in history)
                has_approved_metadata = bool(reviewer and reviewed_at)
                has_approval_signal = has_history_action or (status_ok and has_approved_metadata)
                
                if status_ok and reviewer and reviewed_at and has_approval_signal:
                    if payload.get("upload_ready") is True:
                        human_request_approved = True
                    else:
                        critical_failures.append("Job request review item payload upload_ready is not true.")
                else:
                    critical_failures.append("Job request review item is not human-approved.")
                    
                if payload.get("manual_only") is not True:
                    critical_failures.append("Job request review item manual_only is not true.")
                if payload.get("api_upload_performed") is True:
                    critical_failures.append("Prior API upload detected in job request review item.")
                    api_upload_performed = True
                if payload.get("fine_tune_job_started") is True:
                    critical_failures.append("Prior fine-tune job detected in job request review item.")
                    fine_tune_job_started = True
                if payload.get("deployment_allowed") is True:
                    critical_failures.append("Prior deployment allowed detected in job request review item.")
                    deployment_allowed = True
                    
                request_id = request_id or payload.get("request_id")
                train_path = train_path or payload.get("train_path")
                validation_path = validation_path or payload.get("validation_path")
                train_hash = train_hash or payload.get("train_hash") or payload.get("validation_result", {}).get("train_hash")
                validation_hash = validation_hash or payload.get("validation_hash") or payload.get("validation_result", {}).get("validation_hash")
                
                req_provider = payload.get("provider")
                if req_provider:
                    if provider and req_provider != provider:
                        critical_failures.append(f"Provider mismatch: review item says '{req_provider}', config says '{provider}'")
                    provider = req_provider

        # C. Start approval review item
        start_review_item = None
        if job_start_review_item_id:
            try:
                start_review_item = await self.load_human_review_item(job_start_review_item_id)
            except Exception as e:
                critical_failures.append(f"Failed to load job start review item: {str(e)}")
                
            if start_review_item:
                if start_review_item.get("item_type") != "fine_tune_job_start_approval":
                    critical_failures.append("Review item is not of type 'fine_tune_job_start_approval'.")
                
                status_ok = start_review_item.get("status") == "approved"
                reviewer = start_review_item.get("reviewer")
                reviewed_at = start_review_item.get("reviewed_at")
                
                payload = start_review_item.get("payload") or {}
                history = payload.get("review_history") or []
                has_history_action = any(h.get("action") == "approved" for h in history)
                has_approved_metadata = bool(reviewer and reviewed_at)
                has_approval_signal = has_history_action or (status_ok and has_approved_metadata)
                
                start_authorized = payload.get("start_authorized") is True or status_ok
                
                if status_ok and reviewer and reviewed_at and has_approval_signal and start_authorized:
                    human_start_approved = True
                else:
                    critical_failures.append("Job start review item is not human-approved or start_authorized is not true.")
                    
                if payload.get("upload_ready") is not True:
                    critical_failures.append("Job start review item upload_ready is not true.")
                if payload.get("manual_only") is not True:
                    critical_failures.append("Job start review item manual_only is not true.")
                if payload.get("api_upload_performed") is True:
                    critical_failures.append("Prior API upload detected in job start review item.")
                    api_upload_performed = True
                if payload.get("fine_tune_job_started") is True:
                    critical_failures.append("Prior fine-tune job detected in job start review item.")
                    fine_tune_job_started = True
                if payload.get("deployment_allowed") is True:
                    critical_failures.append("Prior deployment allowed detected in job start review item.")
                    deployment_allowed = True
                    
                request_id = request_id or payload.get("request_id")
                train_path = train_path or payload.get("train_path")
                validation_path = validation_path or payload.get("validation_path")
                train_hash = train_hash or payload.get("train_hash")
                validation_hash = validation_hash or payload.get("validation_hash")
                
                start_provider = payload.get("provider")
                if start_provider:
                    if provider and start_provider != provider:
                        critical_failures.append(f"Provider mismatch: start review item says '{start_provider}', config says '{provider}'")
                    provider = start_provider

        # F. No prior mutation checks on other fields
        def check_source_mutation(src_payload: dict, label: str):
            if src_payload.get("active_runtime") is True:
                critical_failures.append(f"Mutation detected: active_runtime is true in {label}")
            if src_payload.get("deployment_id"):
                critical_failures.append(f"Mutation detected: deployment_id exists in {label}")
            if src_payload.get("model_id"):
                critical_failures.append(f"Mutation detected: model_id exists in {label}")

        if package:
            check_source_mutation(package, "package")
        if req_review_item:
            check_source_mutation(req_review_item.get("payload") or {}, "job request review item")
        if start_review_item:
            check_source_mutation(start_review_item.get("payload") or {}, "job start review item")

        # D. File integrity
        if train_path and validation_path:
            t_path = Path(train_path)
            v_path = Path(validation_path)
            if t_path.exists() and v_path.exists():
                files_exist = True
                actual_train_hash = self.compute_file_hash(train_path)
                actual_val_hash = self.compute_file_hash(validation_path)
                
                train_match = True
                val_match = True
                if train_hash and actual_train_hash != train_hash:
                    train_match = False
                if validation_hash and actual_val_hash != validation_hash:
                    val_match = False
                    
                if train_match and val_match:
                    hashes_match = True
                else:
                    hashes_match = False
                    critical_failures.append("Train or validation file hash mismatch.")
            else:
                if not t_path.exists():
                    critical_failures.append(f"Train file does not exist at: {train_path}")
                if not v_path.exists():
                    critical_failures.append(f"Validation file does not exist at: {validation_path}")
        else:
            critical_failures.append("Train or validation file path is missing.")

        upload_ready_val = False
        if package and package.get("upload_ready") is True:
            upload_ready_val = True
        elif req_review_item and req_review_item.get("payload", {}).get("upload_ready") is True:
            upload_ready_val = True
        elif start_review_item and start_review_item.get("payload", {}).get("upload_ready") is True:
            upload_ready_val = True

        req_start_approved = True
        if job_start_review_item_id:
            req_start_approved = human_start_approved
            
        eligible = (
            len(critical_failures) == 0 and
            len(high_failures) == 0 and
            upload_ready_val is True and
            (human_request_approved is True or not job_request_review_item_id) and
            req_start_approved is True and
            files_exist is True and
            hashes_match is True and
            manual_only is True and
            api_upload_performed is False and
            fine_tune_job_started is False and
            deployment_allowed is False
        )
        
        # When creating request from package only, human_request_approved is false.
        # But we want to fail eligibility check if review item was provided but rejected, or similar.
        # In general, human_request_approved must be True if request review item id was provided.
        if job_request_review_item_id and not human_request_approved:
            eligible = False

        source_summary = {}
        if package_path:
            source_summary["package_path"] = package_path
        if job_request_review_item_id:
            source_summary["job_request_review_item_id"] = job_request_review_item_id
        if job_start_review_item_id:
            source_summary["job_start_review_item_id"] = job_start_review_item_id

        return FineTuneJobStartEligibilityResult(
            eligible=eligible,
            upload_ready=upload_ready_val,
            provider=provider or "generic",
            request_id=request_id,
            job_request_review_item_id=job_request_review_item_id,
            job_start_review_item_id=job_start_review_item_id,
            package_path=package_path,
            train_path=train_path,
            validation_path=validation_path,
            train_hash=train_hash,
            validation_hash=validation_hash,
            files_exist=files_exist,
            hashes_match=hashes_match,
            human_request_approved=human_request_approved,
            human_start_approved=human_start_approved,
            manual_only=manual_only,
            api_upload_performed=api_upload_performed,
            fine_tune_job_started=fine_tune_job_started,
            deployment_allowed=deployment_allowed,
            critical_failures=critical_failures,
            high_failures=high_failures,
            medium_warnings=medium_warnings,
            low_warnings=low_warnings,
            source_summary=source_summary
        )

    async def create_start_approval_request(self, config: FineTuneJobTrackerConfig) -> FineTuneJobTrackingResult:
        eligibility = await self.check_start_eligibility(config)
        
        if not eligibility.upload_ready:
            return FineTuneJobTrackingResult(
                operation="create_start_approval_request",
                success=False,
                message="Cannot create start approval request: package is not upload_ready.",
                eligibility=eligibility
            )
            
        if eligibility.critical_failures:
            return FineTuneJobTrackingResult(
                operation="create_start_approval_request",
                success=False,
                message=f"Cannot create start approval request due to critical failures: {', '.join(eligibility.critical_failures)}",
                eligibility=eligibility
            )
            
        warnings = []
        if not config.job_request_review_item_id:
            warnings.append("Approved fine_tune_job_request review item is recommended before start approval.")
            
        pre_start_checks = [
            "Verify dataset passed Prompt 19 gates",
            "Verify human approved Prompt 20 request package",
            "Verify train/validation files exist locally",
            "Verify SHA-256 hashes match approved package",
            "Confirm that no automated upload or job start has run",
            "Confirm manual-only mode is active"
        ]
        
        instructions = [
            "Review package and checklist.",
            f"Verify {eligibility.provider.upper() if eligibility.provider != 'generic' else 'provider'} account, region, quota, and model availability manually.",
            "Upload train/validation files manually through approved internal process.",
            "Record manually created file IDs in this system.",
            "Never deploy model directly."
        ]
        
        next_steps = [
            "Obtain human approval on this start request.",
            "Perform manual upload of dataset files.",
            "Record manually created file IDs.",
            "Perform manual fine-tuning job start.",
            "Record manually created job ID."
        ]
        
        request_id = eligibility.request_id or str(uuid.uuid4())[:8]
        
        payload = {
            "source": "fine_tune_job_tracker",
            "request_id": request_id,
            "provider": eligibility.provider,
            "job_request_package_path": config.job_request_package_path,
            "job_request_review_item_id": config.job_request_review_item_id,
            "train_path": eligibility.train_path,
            "validation_path": eligibility.validation_path,
            "train_hash": eligibility.train_hash,
            "validation_hash": eligibility.validation_hash,
            "upload_ready": eligibility.upload_ready,
            "manual_only": True,
            "api_upload_performed": False,
            "fine_tune_job_started": False,
            "deployment_allowed": False,
            "start_authorized": False,
            "required_human_approval": True,
            "required_pre_start_checks": pre_start_checks,
            "manual_upload_instructions": instructions,
            "validation_summary": {
                "critical_failures": eligibility.critical_failures,
                "high_failures": eligibility.high_failures,
                "medium_warnings": eligibility.medium_warnings,
                "low_warnings": eligibility.low_warnings
            },
            "next_steps": next_steps
        }
        
        review_item_id = None
        if not config.dry_run:
            review_item_id = await self.repository.save_human_review_item(
                item_type="fine_tune_job_start_approval",
                status="pending",
                payload=payload
            )
            
        tracking_id = "tr_" + str(uuid.uuid4())[:8]
        
        history = []
        history = self.append_audit(
            history,
            operation="create_tracking",
            actor=config.actor or "system",
            reason=config.reason or "Initialized tracking record.",
            previous_status=None,
            new_status="requested"
        )
        
        history = self.append_audit(
            history,
            operation="request_start_approval",
            actor=config.actor or "system",
            reason=config.reason or "Submitted job start approval request.",
            previous_status="requested",
            new_status="start_approval_pending"
        )
        
        record = FineTuneJobTrackingRecord(
            tracking_id=tracking_id,
            request_id=request_id,
            provider=eligibility.provider,
            status="start_approval_pending",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            actor=config.actor,
            job_request_package_path=config.job_request_package_path,
            job_request_review_item_id=config.job_request_review_item_id,
            job_start_review_item_id=review_item_id,
            train_path=eligibility.train_path,
            validation_path=eligibility.validation_path,
            train_hash=eligibility.train_hash,
            validation_hash=eligibility.validation_hash,
            manual_only=True,
            api_upload_performed=False,
            fine_tune_job_started=False,
            deployment_allowed=False,
            start_authorized=False,
            audit_history=history,
            validation_summary=payload["validation_summary"],
            metadata={}
        )
        
        if not config.dry_run:
            await self.repository.save_human_review_item(
                item_type="fine_tune_job_tracking",
                status="start_approval_pending",
                payload=record.model_dump(mode="json")
            )
            
        return FineTuneJobTrackingResult(
            tracking_id=tracking_id,
            request_id=request_id,
            operation="create_start_approval_request",
            previous_status="requested",
            new_status="start_approval_pending",
            success=True,
            message="Start approval request created successfully.",
            eligibility=eligibility,
            record=record,
            review_item_id=review_item_id,
            warnings=warnings
        )

    async def record_manual_upload(self, config: FineTuneJobTrackerConfig) -> FineTuneJobTrackingResult:
        if not config.job_start_review_item_id:
            raise ValueError("job_start_review_item_id is required.")
        if not config.provider_file_id:
            raise ValueError("provider_file_id is required.")
            
        start_review_item = await self.load_human_review_item(config.job_start_review_item_id)
        payload = start_review_item.get("payload") or {}
        
        provider = payload.get("provider") or config.provider
        if provider != "generic" and not config.provider_validation_file_id:
            raise ValueError("provider_validation_file_id is required.")
            
        eligibility = await self.check_start_eligibility(config)
        if not eligibility.human_start_approved:
            return FineTuneJobTrackingResult(
                operation="record_manual_upload",
                success=False,
                message="Start approval review item is not approved or start_authorized is not true.",
                eligibility=eligibility
            )
            
        if payload.get("api_upload_performed") is True:
            raise ValueError("Prior API upload detected in start approval item.")
            
        tracking_items = await self.repository.query_human_review_items({"item_type": "fine_tune_job_tracking"})
        tracking_item = None
        for item in tracking_items:
            if item.get("payload", {}).get("job_start_review_item_id") == config.job_start_review_item_id:
                tracking_item = item
                break
                
        history = []
        if tracking_item:
            record_payload = tracking_item.get("payload") or {}
            record = FineTuneJobTrackingRecord(**record_payload)
            prev_status = record.status
        else:
            tracking_id = "tr_" + str(uuid.uuid4())[:8]
            record = FineTuneJobTrackingRecord(
                tracking_id=tracking_id,
                request_id=payload.get("request_id") or str(uuid.uuid4())[:8],
                provider=provider or "generic",
                status="requested",
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
                actor=config.actor,
                job_request_package_path=payload.get("job_request_package_path"),
                job_request_review_item_id=payload.get("job_request_review_item_id"),
                job_start_review_item_id=config.job_start_review_item_id,
                train_path=payload.get("train_path"),
                validation_path=payload.get("validation_path"),
                train_hash=payload.get("train_hash"),
                validation_hash=payload.get("validation_hash"),
                manual_only=True,
                api_upload_performed=False,
                fine_tune_job_started=False,
                deployment_allowed=False,
                start_authorized=True,
                audit_history=history,
                validation_summary=payload.get("validation_summary") or {},
                metadata={}
            )
            record.audit_history = self.append_audit(
                record.audit_history,
                operation="create_tracking",
                actor=config.actor or "system",
                reason="Initialized tracking record during manual upload recording.",
                previous_status=None,
                new_status="requested"
            )
            prev_status = "requested"
            
        current_status = prev_status
        if current_status == "requested":
            self.validate_status_transition(current_status, "start_approval_pending")
            record.status = "start_approval_pending"
            record.audit_history = self.append_audit(
                record.audit_history,
                operation="request_start_approval",
                actor=config.actor or "system",
                reason="Auto transition to start_approval_pending.",
                previous_status="requested",
                new_status="start_approval_pending"
            )
            current_status = "start_approval_pending"
            
        if current_status == "start_approval_pending":
            self.validate_status_transition(current_status, "start_approved")
            record.status = "start_approved"
            record.audit_history = self.append_audit(
                record.audit_history,
                operation="approve_start",
                actor=config.actor or "system",
                reason="Auto transition to start_approved.",
                previous_status="start_approval_pending",
                new_status="start_approved"
            )
            current_status = "start_approved"
            
        self.validate_status_transition(current_status, "files_uploaded_manual")
        
        record.status = "files_uploaded_manual"
        record.provider_file_id = config.provider_file_id
        record.provider_validation_file_id = config.provider_validation_file_id
        record.api_upload_performed = False
        record.start_authorized = True
        record.metadata["manual_upload_recorded"] = True
        record.updated_at = datetime.now(timezone.utc).isoformat()
        
        record.audit_history = self.append_audit(
            record.audit_history,
            operation="record_manual_upload",
            actor=config.actor or "system",
            reason=config.reason or "Recorded manual file upload.",
            previous_status=current_status,
            new_status="files_uploaded_manual"
        )
        
        if not config.dry_run:
            payload_dict = record.model_dump(mode="json")
            if tracking_item:
                await self.repository.save_human_review_item(
                    id=tracking_item["id"],
                    item_type="fine_tune_job_tracking",
                    status="files_uploaded_manual",
                    payload=payload_dict,
                    reviewer=config.actor,
                    review_notes=config.reason,
                    created_at=tracking_item["created_at"],
                    reviewed_at=datetime.now(timezone.utc)
                )
            else:
                await self.repository.save_human_review_item(
                    item_type="fine_tune_job_tracking",
                    status="files_uploaded_manual",
                    payload=payload_dict
                )
                
        return FineTuneJobTrackingResult(
            tracking_id=record.tracking_id,
            request_id=record.request_id,
            operation="record_manual_upload",
            previous_status=prev_status,
            new_status="files_uploaded_manual",
            success=True,
            message="Manual upload recorded successfully.",
            record=record
        )

    async def record_manual_job_start(self, config: FineTuneJobTrackerConfig) -> FineTuneJobTrackingResult:
        if not config.job_start_review_item_id:
            raise ValueError("job_start_review_item_id is required.")
        if not config.provider_job_id:
            raise ValueError("provider_job_id is required.")
            
        start_review_item = await self.load_human_review_item(config.job_start_review_item_id)
        payload = start_review_item.get("payload") or {}
        
        eligibility = await self.check_start_eligibility(config)
        if not eligibility.human_start_approved:
            return FineTuneJobTrackingResult(
                operation="record_manual_job_start",
                success=False,
                message="Start approval review item is not approved or start_authorized is not true.",
                eligibility=eligibility
            )
            
        tracking_items = await self.repository.query_human_review_items({"item_type": "fine_tune_job_tracking"})
        tracking_item = None
        for item in tracking_items:
            if item.get("payload", {}).get("job_start_review_item_id") == config.job_start_review_item_id:
                tracking_item = item
                break
                
        history = []
        if tracking_item:
            record_payload = tracking_item.get("payload") or {}
            record = FineTuneJobTrackingRecord(**record_payload)
            prev_status = record.status
        else:
            tracking_id = "tr_" + str(uuid.uuid4())[:8]
            record = FineTuneJobTrackingRecord(
                tracking_id=tracking_id,
                request_id=payload.get("request_id") or str(uuid.uuid4())[:8],
                provider=payload.get("provider") or config.provider or "generic",
                status="requested",
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
                actor=config.actor,
                job_request_package_path=payload.get("job_request_package_path"),
                job_request_review_item_id=payload.get("job_request_review_item_id"),
                job_start_review_item_id=config.job_start_review_item_id,
                train_path=payload.get("train_path"),
                validation_path=payload.get("validation_path"),
                train_hash=payload.get("train_hash"),
                validation_hash=payload.get("validation_hash"),
                manual_only=True,
                api_upload_performed=False,
                fine_tune_job_started=False,
                deployment_allowed=False,
                start_authorized=True,
                audit_history=history,
                validation_summary=payload.get("validation_summary") or {},
                metadata={}
            )
            record.audit_history = self.append_audit(
                record.audit_history,
                operation="create_tracking",
                actor=config.actor or "system",
                reason="Initialized tracking record during manual job start recording.",
                previous_status=None,
                new_status="requested"
            )
            prev_status = "requested"

        is_upload_recorded = record.metadata.get("manual_upload_recorded") is True or record.status == "files_uploaded_manual"
        warnings = []
        if not is_upload_recorded:
            if not config.reason or not config.reason.strip():
                raise ValueError("Manual upload was not recorded and no override reason was provided.")
            else:
                warnings.append("Recording manual job start without recording manual file upload first.")

        current_status = prev_status
        if current_status == "requested":
            self.validate_status_transition(current_status, "start_approval_pending")
            record.status = "start_approval_pending"
            record.audit_history = self.append_audit(
                record.audit_history,
                operation="request_start_approval",
                actor=config.actor or "system",
                reason="Auto transition to start_approval_pending.",
                previous_status="requested",
                new_status="start_approval_pending"
            )
            current_status = "start_approval_pending"
            
        if current_status == "start_approval_pending":
            self.validate_status_transition(current_status, "start_approved")
            record.status = "start_approved"
            record.audit_history = self.append_audit(
                record.audit_history,
                operation="approve_start",
                actor=config.actor or "system",
                reason="Auto transition to start_approved.",
                previous_status="start_approval_pending",
                new_status="start_approved"
            )
            current_status = "start_approved"
            
        if current_status == "start_approved":
            self.validate_status_transition(current_status, "files_uploaded_manual")
            record.status = "files_uploaded_manual"
            record.audit_history = self.append_audit(
                record.audit_history,
                operation="record_manual_upload_auto",
                actor=config.actor or "system",
                reason="Automatic transition: file upload step bypassed.",
                previous_status="start_approved",
                new_status="files_uploaded_manual"
            )
            current_status = "files_uploaded_manual"
            
        self.validate_status_transition(current_status, "job_started_manual")
        
        record.status = "job_started_manual"
        record.provider_job_id = config.provider_job_id
        record.fine_tune_job_started = False
        record.start_authorized = True
        record.metadata["manual_job_start_recorded"] = True
        record.updated_at = datetime.now(timezone.utc).isoformat()
        
        record.audit_history = self.append_audit(
            record.audit_history,
            operation="record_manual_job_start",
            actor=config.actor or "system",
            reason=config.reason or "Recorded manual job start.",
            previous_status=current_status,
            new_status="job_started_manual"
        )
        
        if not config.dry_run:
            payload_dict = record.model_dump(mode="json")
            if tracking_item:
                await self.repository.save_human_review_item(
                    id=tracking_item["id"],
                    item_type="fine_tune_job_tracking",
                    status="job_started_manual",
                    payload=payload_dict,
                    reviewer=config.actor,
                    review_notes=config.reason,
                    created_at=tracking_item["created_at"],
                    reviewed_at=datetime.now(timezone.utc)
                )
            else:
                await self.repository.save_human_review_item(
                    item_type="fine_tune_job_tracking",
                    status="job_started_manual",
                    payload=payload_dict
                )
                
        return FineTuneJobTrackingResult(
            tracking_id=record.tracking_id,
            request_id=record.request_id,
            operation="record_manual_job_start",
            previous_status=prev_status,
            new_status="job_started_manual",
            success=True,
            message="Manual job start recorded successfully.",
            record=record,
            warnings=warnings
        )

    async def get_tracking_record_item(self, tracking_id: str) -> dict | None:
        items = await self.repository.query_human_review_items({"item_type": "fine_tune_job_tracking"})
        for item in items:
            payload = item.get("payload") or {}
            if payload.get("tracking_id") == tracking_id:
                return item
        return None

    async def get_tracking_record(self, tracking_id: str) -> dict:
        item = await self.get_tracking_record_item(tracking_id)
        if not item:
            raise ValueError(f"Tracking record not found: {tracking_id}")
        return item.get("payload") or {}

    async def list_tracking_records(self, status: str | None = None, limit: int = 50) -> list[dict]:
        filters = {"item_type": "fine_tune_job_tracking"}
        if status:
            filters["status"] = status
        items = await self.repository.query_human_review_items(filters)
        items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return [item.get("payload") or {} for item in items[:limit]]

    async def update_manual_status(
        self,
        tracking_id: str,
        new_status: str,
        actor: str,
        reason: str,
        provider_model_id: str | None = None
    ) -> FineTuneJobTrackingResult:
        if not tracking_id:
            raise ValueError("tracking_id is required.")
        if not actor or not actor.strip():
            raise ValueError("actor is required.")
        if not reason or not reason.strip():
            raise ValueError("reason is required.")
            
        allowed_new_statuses = {
            "running_manual",
            "succeeded_manual",
            "failed_manual",
            "cancelled_manual",
            "blocked",
            "archived"
        }
        if new_status not in allowed_new_statuses:
            raise ValueError(f"Status '{new_status}' is not a valid manual tracking status for update_manual_status.")

        record_item = await self.get_tracking_record_item(tracking_id)
        if not record_item:
            raise ValueError(f"Tracking record not found: {tracking_id}")
            
        payload = record_item.get("payload") or {}
        record = FineTuneJobTrackingRecord(**payload)
        
        prev_status = record.status
        self.validate_status_transition(prev_status, new_status)
        
        record.status = new_status
        record.actor = actor
        record.updated_at = datetime.now(timezone.utc).isoformat()
        
        if new_status == "succeeded_manual":
            if provider_model_id:
                record.provider_model_id = provider_model_id
            record.deployment_allowed = False
            record.metadata["active_runtime"] = False
            
        record.audit_history = self.append_audit(
            record.audit_history,
            operation="update_manual_status",
            actor=actor,
            reason=reason,
            previous_status=prev_status,
            new_status=new_status
        )
        
        record_item["payload"] = record.model_dump(mode="json")
        record_item["status"] = new_status
        
        await self.repository.save_human_review_item(
            id=record_item["id"],
            item_type="fine_tune_job_tracking",
            payload=record_item["payload"],
            status=new_status,
            reviewer=actor,
            review_notes=reason,
            created_at=record_item["created_at"],
            reviewed_at=datetime.now(timezone.utc)
        )
        
        return FineTuneJobTrackingResult(
            tracking_id=tracking_id,
            request_id=record.request_id,
            operation="update_manual_status",
            previous_status=prev_status,
            new_status=new_status,
            success=True,
            message=f"Status updated to {new_status} successfully.",
            record=record
        )

    async def generate_tracking_report(self, tracking_id: str, output_dir: str | Path = "data/fine_tune_job_tracking") -> tuple[str, str]:
        record_item = await self.get_tracking_record_item(tracking_id)
        if not record_item:
            raise ValueError(f"Tracking record not found: {tracking_id}")
            
        payload = record_item.get("payload") or {}
        record = FineTuneJobTrackingRecord(**payload)
        
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        
        json_path = out_dir / f"fine_tune_job_tracking_{tracking_id}.json"
        md_path = out_dir / f"fine_tune_job_tracking_{tracking_id}.md"
        
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(record.model_dump(mode="json"), f, indent=2)
            
        audit_rows = []
        for a in record.audit_history:
            audit_rows.append(
                f"| {a.get('operation')} | {a.get('actor')} | {a.get('previous_status') or 'N/A'} | {a.get('new_status')} | {a.get('reason')} | {a.get('timestamp')} |"
            )
            
        md_content = f"""# Dana Fine-Tune Job Tracking Report

Tracking ID: {record.tracking_id}
Request ID: {record.request_id}
Provider: {record.provider}
Status: {record.status}
Generated: {datetime.now(timezone.utc).isoformat()}

## Executive Summary
- Upload ready: {record.upload_ready}
- Start authorized: {record.start_authorized}
- Manual upload recorded: {record.metadata.get("manual_upload_recorded") is True}
- Manual job start recorded: {record.metadata.get("manual_job_start_recorded") is True}
- Deployment allowed: {record.deployment_allowed}
- Active runtime: {record.metadata.get("active_runtime") is True}
- Current status: {record.status}

## Dataset Files
- Train path: {record.train_path or "N/A"}
- Validation path: {record.validation_path or "N/A"}
- Train SHA-256: {record.train_hash or "N/A"}
- Validation SHA-256: {record.validation_hash or "N/A"}

## Provider References
- Provider file ID: {record.provider_file_id or "N/A"}
- Provider validation file ID: {record.provider_validation_file_id or "N/A"}
- Provider job ID: {record.provider_job_id or "N/A"}
- Provider model ID: {record.provider_model_id or "N/A"}

## Safety Controls
- System did not upload files: True
- System did not start fine-tune job: True
- System did not deploy model: True
- System did not modify live prompt: True
- Future eval/replay/simulation/canary required before any production use: True

## Audit History
| Operation | Actor | Previous Status | New Status | Reason | Timestamp |
| --- | --- | --- | --- | --- | --- |
"""
        md_content += "\n".join(audit_rows) + "\n\n"
        md_content += """## Required Next Steps
- If running: monitor manually in provider console
- If succeeded: create future model evaluation package
- If failed: document failure reason
- Do not deploy without eval, replay, simulation, prompt versioning, and canary gates
- Do not mark model active manually
"""
        
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
            
        return str(json_path), str(md_path)
