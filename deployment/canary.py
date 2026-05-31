"""Dana Prompt Canary Rollout System.

Implements candidate eligibility checking, plan creation, status transitions,
deterministic routing, and reports generation.
"""

from __future__ import annotations

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from storage.repository import Repository
from storage.schemas import DeploymentExperiment, PromptVersion


class CanaryEligibilityResult:
    def __init__(
        self,
        eligible: bool,
        candidate_prompt_version_id: str,
        prompt_name: str,
        failures: list[str],
        warnings: list[str],
        gate_summary: dict[str, Any],
        candidate_metadata: dict[str, Any],
    ) -> None:
        self.eligible = eligible
        self.candidate_prompt_version_id = candidate_prompt_version_id
        self.prompt_name = prompt_name
        self.failures = failures
        self.warnings = warnings
        self.gate_summary = gate_summary
        self.candidate_metadata = candidate_metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "candidate_prompt_version_id": self.candidate_prompt_version_id,
            "prompt_name": self.prompt_name,
            "failures": self.failures,
            "warnings": self.warnings,
            "gate_summary": self.gate_summary,
            "candidate_metadata": self.candidate_metadata,
        }


class CanaryPlan:
    def __init__(
        self,
        experiment_name: str,
        prompt_name: str,
        control_prompt_version_id: str,
        candidate_prompt_version_id: str,
        traffic_percentage: float,
        max_traffic_percentage: float,
        status: str,
        created_by: str,
        start_conditions: list[str],
        stop_conditions: list[str],
        rollback_plan: dict[str, Any],
        metadata: dict[str, Any],
        experiment_id: Optional[str] = None,
        approved_by: Optional[str] = None,
    ) -> None:
        self.experiment_id = experiment_id
        self.experiment_name = experiment_name
        self.prompt_name = prompt_name
        self.control_prompt_version_id = control_prompt_version_id
        self.candidate_prompt_version_id = candidate_prompt_version_id
        self.traffic_percentage = traffic_percentage
        self.max_traffic_percentage = max_traffic_percentage
        self.status = status
        self.created_by = created_by
        self.approved_by = approved_by
        self.start_conditions = start_conditions
        self.stop_conditions = stop_conditions
        self.rollback_plan = rollback_plan
        self.metadata = metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "experiment_name": self.experiment_name,
            "prompt_name": self.prompt_name,
            "control_prompt_version_id": self.control_prompt_version_id,
            "candidate_prompt_version_id": self.candidate_prompt_version_id,
            "traffic_percentage": self.traffic_percentage,
            "max_traffic_percentage": self.max_traffic_percentage,
            "status": self.status,
            "created_by": self.created_by,
            "approved_by": self.approved_by,
            "start_conditions": self.start_conditions,
            "stop_conditions": self.stop_conditions,
            "rollback_plan": self.rollback_plan,
            "metadata": self.metadata,
        }


class CanaryDecision:
    def __init__(
        self,
        use_candidate: bool,
        prompt_version_id: Optional[str],
        reason: str,
        bucket_value: float,
        traffic_percentage: float,
        prompt_name: str,
        experiment_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.use_candidate = use_candidate
        self.prompt_version_id = prompt_version_id
        self.reason = reason
        self.bucket_value = bucket_value
        self.traffic_percentage = traffic_percentage
        self.experiment_id = experiment_id
        self.prompt_name = prompt_name
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "use_candidate": self.use_candidate,
            "prompt_version_id": self.prompt_version_id,
            "reason": self.reason,
            "bucket_value": self.bucket_value,
            "traffic_percentage": self.traffic_percentage,
            "experiment_id": self.experiment_id,
            "prompt_name": self.prompt_name,
            "metadata": self.metadata,
        }


class CanaryOperationResult:
    def __init__(
        self,
        experiment_id: str,
        operation: str,
        new_status: str,
        success: bool,
        message: str,
        warnings: list[str],
        previous_status: Optional[str] = None,
        report_json_path: Optional[str] = None,
        report_markdown_path: Optional[str] = None,
    ) -> None:
        self.experiment_id = experiment_id
        self.operation = operation
        self.previous_status = previous_status
        self.new_status = new_status
        self.success = success
        self.message = message
        self.warnings = warnings
        self.report_json_path = report_json_path
        self.report_markdown_path = report_markdown_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "operation": self.operation,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "success": self.success,
            "message": self.message,
            "warnings": self.warnings,
            "report_json_path": self.report_json_path,
            "report_markdown_path": self.report_markdown_path,
        }


class CanaryReport:
    def __init__(
        self,
        experiment_id: str,
        experiment_name: str,
        prompt_name: str,
        status: str,
        control_prompt_version_id: str,
        candidate_prompt_version_id: str,
        traffic_percentage: float,
        gate_summary: dict[str, Any],
        metrics_summary: dict[str, Any],
        rollback_plan: dict[str, Any],
        warnings: list[str],
        started_at: Optional[str] = None,
        ended_at: Optional[str] = None,
    ) -> None:
        self.experiment_id = experiment_id
        self.experiment_name = experiment_name
        self.prompt_name = prompt_name
        self.status = status
        self.control_prompt_version_id = control_prompt_version_id
        self.candidate_prompt_version_id = candidate_prompt_version_id
        self.traffic_percentage = traffic_percentage
        self.started_at = started_at
        self.ended_at = ended_at
        self.gate_summary = gate_summary
        self.metrics_summary = metrics_summary
        self.rollback_plan = rollback_plan
        self.warnings = warnings

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "experiment_name": self.experiment_name,
            "prompt_name": self.prompt_name,
            "status": self.status,
            "control_prompt_version_id": self.control_prompt_version_id,
            "candidate_prompt_version_id": self.candidate_prompt_version_id,
            "traffic_percentage": self.traffic_percentage,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "gate_summary": self.gate_summary,
            "metrics_summary": self.metrics_summary,
            "rollback_plan": self.rollback_plan,
            "warnings": self.warnings,
        }


class CanaryManager:
    """Manages prompt canary rollouts and control plane registry."""

    def __init__(
        self,
        repository: Optional[Repository] = None,
        prompt_version_manager: Optional[Any] = None,
    ) -> None:
        self.repository = repository or Repository()
        self.prompt_version_manager = prompt_version_manager

    def _extract_candidate_metadata(self, prompt_version_record: dict) -> dict:
        qa = prompt_version_record.get("qa_thresholds") or {}
        outer = qa.get("metadata") or {}
        inner = outer.get("metadata") or {}

        merged = {}
        merged.update(outer)
        merged.update(inner)

        # Preserve prompt name from qa_thresholds if not already set.
        if "prompt_name" not in merged and qa.get("prompt_name"):
            merged["prompt_name"] = qa.get("prompt_name")

        return merged

    def _compute_content_hash(self, content: str) -> str:
        """Compute stable SHA-256 hash, using PromptVersionManager when available."""
        pvm = self.prompt_version_manager
        if pvm is None:
            try:
                from prompts.versioning import PromptVersionManager
                pvm = PromptVersionManager(repository=self.repository)
            except ImportError:
                pass

        if pvm is not None:
            return pvm.compute_content_hash(content)

        # local helper fallback
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip() for line in normalized.split("\n")]
        normalized_content = "\n".join(lines)
        return hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()

    def deterministic_bucket(self, key: str) -> float:
        """Deterministically map a key string to a bucket float in [0.0, 100.0)."""
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        val = int(h[:8], 16)
        return (val % 10000) / 100.0

    def validate_traffic_percentage(
        self, traffic_percentage: float, max_traffic_percentage: float
    ) -> None:
        """Validate traffic bounds."""
        if traffic_percentage <= 0.0:
            raise ValueError("Traffic percentage must be greater than 0")
        if traffic_percentage > max_traffic_percentage:
            raise ValueError(
                f"Traffic percentage {traffic_percentage}% exceeds max configured limit of {max_traffic_percentage}%"
            )

    def build_rollback_plan(
        self, experiment_name: str, control_prompt_version_id: str
    ) -> dict[str, Any]:
        """Generate a rollback plan for the deployment registry."""
        return {
            "action": "rollback",
            "target_status": "rolled_back",
            "traffic_allocation": {
                "candidate_percent": 0.0,
                "control_percent": 100.0,
                "control_prompt_version_id": control_prompt_version_id,
            },
            "preserve_candidate_record": True,
            "generate_rollback_report": True,
            "requires_manager_review": True,
            "notes": f"Rollback plan for {experiment_name}. Route all traffic to control version {control_prompt_version_id}.",
        }

    async def check_candidate_eligibility(
        self, candidate_prompt_version_id: str
    ) -> CanaryEligibilityResult:
        """Verify if a candidate prompt version is eligible for rollout."""
        failures: list[str] = []
        warnings: list[str] = []

        pv = await self.repository.get_prompt_version(candidate_prompt_version_id)
        if not pv:
            failures.append(f"PromptVersion with ID '{candidate_prompt_version_id}' does not exist")
            return CanaryEligibilityResult(
                eligible=False,
                candidate_prompt_version_id=candidate_prompt_version_id,
                prompt_name="",
                failures=failures,
                warnings=warnings,
                gate_summary={},
                candidate_metadata={},
            )

        # Retrieve mapped extra fields stored in qa_thresholds dict (for db compatibility)
        qa = pv.get("qa_thresholds") or {}
        content = qa.get("content") or ""
        metadata = self._extract_candidate_metadata(pv)

        # 1. Verify status is candidate
        canary_status = pv.get("canary_status")
        if canary_status != "candidate":
            failures.append(f"PromptVersion canary_status is '{canary_status}', must be 'candidate'")

        # 2. Content not empty
        if not content.strip():
            failures.append("Prompt content is empty")

        # 3. Prompt name exists
        file_path = pv.get("file_path") or ""
        prompt_name = metadata.get("prompt_name")
        if not prompt_name:
            if file_path:
                prompt_name = Path(file_path).stem
            else:
                failures.append("Prompt name could not be resolved (missing prompt_name metadata and file_path)")

        # 4. Check creation metadata
        created_from = metadata.get("created_from")
        patch_review_item_ids = metadata.get("patch_review_item_ids")
        if created_from != "prompt_patch_preview" and not patch_review_item_ids:
            failures.append("Candidate must be created from 'prompt_patch_preview' or have non-empty patch_review_item_ids")

        # 5. Runtime flags
        if metadata.get("runtime_changed") is True:
            failures.append("Candidate has runtime_changed=True")
        if metadata.get("active_runtime") is True:
            failures.append("Candidate has active_runtime=True")

        # 6. Gate checks
        gate_result = metadata.get("gate_result")
        gate_summary = {}
        if not gate_result:
            failures.append("Gate result metadata is missing")
        else:
            gate_summary = dict(gate_result)
            if not gate_result.get("passed"):
                failures.append("Gate result 'passed' check is false")
            if not gate_result.get("prompt_validation_passed"):
                failures.append("Gate result 'prompt_validation_passed' check is false")
            if not gate_result.get("transcript_replay_passed"):
                failures.append("Gate result 'transcript_replay_passed' check is false")
            if not gate_result.get("prospect_simulations_passed"):
                failures.append("Gate result 'prospect_simulations_passed' check is false")

            # Check eval gate skipped vs failed
            eval_cases_passed = gate_result.get("eval_cases_passed")
            eval_cases_present = gate_result.get("eval_cases_present", False)
            if not eval_cases_present:
                warnings.append("Evaluation gate skipped because no EvalCases were present")
            elif not eval_cases_passed:
                failures.append("Gate result 'eval_cases_passed' check is false")

        # 7. Hash checks
        source_prompt_hash = metadata.get("source_prompt_hash")
        patched_prompt_hash = metadata.get("patched_prompt_hash")

        if not source_prompt_hash:
            failures.append("source_prompt_hash metadata is missing")
        if not patched_prompt_hash:
            failures.append("patched_prompt_hash metadata is missing")
        if not patch_review_item_ids:
            failures.append("patch_review_item_ids is missing or empty")

        # Verify patched_prompt_hash matches content hash recomputed
        if content:
            computed_hash = self._compute_content_hash(content)
            if computed_hash != patched_prompt_hash:
                failures.append(f"Content hash '{computed_hash}' does not match patched_prompt_hash '{patched_prompt_hash}'")

        # Warning for stale source prompt
        if file_path:
            resolved_path = Path(file_path)
            if not resolved_path.is_absolute():
                resolved_path = Path.cwd() / resolved_path
            if resolved_path.exists():
                try:
                    live_content = resolved_path.read_text(encoding="utf-8")
                    live_hash = self._compute_content_hash(live_content)
                    if live_hash != source_prompt_hash:
                        warnings.append("Source prompt hash is stale compared to the current live prompt file on disk")
                except Exception as e:
                    warnings.append(f"Failed to read live prompt file for stale check: {e}")

        # Warning if prompt validation has critical warnings
        validation_warnings = metadata.get("validation_warnings")
        if validation_warnings:
            warnings.extend(validation_warnings)

        eligible = len(failures) == 0
        return CanaryEligibilityResult(
            eligible=eligible,
            candidate_prompt_version_id=candidate_prompt_version_id,
            prompt_name=prompt_name or "",
            failures=failures,
            warnings=warnings,
            gate_summary=gate_summary,
            candidate_metadata=metadata,
        )

    async def create_canary_plan(
        self,
        candidate_prompt_version_id: str,
        experiment_name: str,
        created_by: str,
        traffic_percentage: float = 1.0,
        max_traffic_percentage: float = 10.0,
        control_prompt_version_id: str | None = None,
        approval_notes: str | None = None,
    ) -> CanaryPlan:
        """Create a planned DeploymentExperiment for canary rollout."""
        # 1. Eligibility Check
        eligibility = await self.check_candidate_eligibility(candidate_prompt_version_id)
        if not eligibility.eligible:
            raise ValueError(f"Candidate prompt version is not eligible: {eligibility.failures}")

        self.validate_traffic_percentage(traffic_percentage, max_traffic_percentage)

        # 2. Resolve Control Prompt Version ID
        resolved_control_id = control_prompt_version_id
        if not resolved_control_id:
            # Query active snapshots/versions
            all_versions = await self.repository.query_prompt_versions({"canary_status": "active"})
            # Filter by prompt name / file path
            for v in all_versions:
                v_path = v.get("file_path") or ""
                v_name = Path(v_path).stem
                if v_name == eligibility.prompt_name:
                    resolved_control_id = v["id"]
                    break

        # Fallback stale source hash check warning if no active control found
        start_warnings = []
        if not resolved_control_id:
            start_warnings.append("No active control version found in repository. Plan requires database mapping.")

        # 3. Create Deployment Record
        rollback_plan = self.build_rollback_plan(experiment_name, resolved_control_id or "default")

        start_conditions = [
            "candidate PromptVersion exists",
            "candidate gate_result passed",
            "human approval required",
            "canary must be explicitly started",
            "runtime flag required before routing can use candidate",
        ]
        stop_conditions = [
            "critical compliance failure",
            "DNC or wrong-number failure",
            "transfer-before-consent failure",
            "price quote failure",
            "licensed/human claim failure",
            "QA score below threshold",
            "complaint or manager override",
            "eval/replay/simulation regression",
            "manual rollback",
        ]

        # Audit History
        created_at = datetime.now(timezone.utc)
        audit_history = [
            {
                "operation": "create_plan",
                "actor": created_by,
                "reason": "Create initial planned experiment",
                "previous_status": None,
                "new_status": "planned",
                "timestamp": created_at.isoformat(),
            }
        ]

        metrics_field = {
            "prompt_name": eligibility.prompt_name,
            "control_prompt_version_id": resolved_control_id or "",
            "created_by": created_by,
            "created_at": created_at.isoformat(),
            "audit_history": audit_history,
            "rollback_plan": rollback_plan,
            "start_conditions": start_conditions,
            "stop_conditions": stop_conditions,
            "traffic_policy": {
                "max_traffic_percentage": max_traffic_percentage,
                "initial_percentage": traffic_percentage,
            },
            "eligibility_summary": eligibility.to_dict(),
            "runtime_default_changed": False,
            "requires_explicit_runtime_flag": True,
            "approval_notes": approval_notes or "",
        }

        # Save to DB
        exp_id = await self.repository.save_deployment_experiment(
            experiment_name=experiment_name,
            prompt_version_id=candidate_prompt_version_id,
            traffic_percent=traffic_percentage,
            status="planned",
            metrics=metrics_field,
            started_at=None,
            ended_at=None,
        )

        return CanaryPlan(
            experiment_id=exp_id,
            experiment_name=experiment_name,
            prompt_name=eligibility.prompt_name,
            control_prompt_version_id=resolved_control_id or "",
            candidate_prompt_version_id=candidate_prompt_version_id,
            traffic_percentage=traffic_percentage,
            max_traffic_percentage=max_traffic_percentage,
            status="planned",
            created_by=created_by,
            start_conditions=start_conditions,
            stop_conditions=stop_conditions,
            rollback_plan=rollback_plan,
            metadata=metrics_field,
        )

    async def _transition_status(
        self,
        experiment_id: str,
        actor: str,
        reason: str,
        allowed_from: list[str],
        target_status: str,
        started_at: Optional[datetime] = None,
        ended_at: Optional[datetime] = None,
        metrics_update: Optional[dict[str, Any]] = None,
    ) -> CanaryOperationResult:
        """Performs a validated status transition for a DeploymentExperiment."""
        exp = await self.repository.get_deployment_experiment(experiment_id)
        if not exp:
            raise ValueError(f"Canary experiment '{experiment_id}' does not exist")

        prev_status = exp.get("status", "planned")
        if prev_status not in allowed_from:
            raise ValueError(
                f"Invalid transition: Cannot move experiment from status '{prev_status}' to '{target_status}'"
            )

        metrics = exp.get("metrics") or {}
        audit_history = metrics.get("audit_history") or []

        timestamp = datetime.now(timezone.utc).isoformat()
        audit_history.append(
            {
                "operation": f"transition_to_{target_status}",
                "actor": actor,
                "reason": reason,
                "previous_status": prev_status,
                "new_status": target_status,
                "timestamp": timestamp,
            }
        )

        # Update metrics dict
        metrics["audit_history"] = audit_history
        if metrics_update:
            metrics.update(metrics_update)

        # Build kwargs for schema update
        update_kwargs = {
            "id": experiment_id,
            "experiment_name": exp.get("experiment_name"),
            "prompt_version_id": exp.get("prompt_version_id"),
            "traffic_percent": exp.get("traffic_percent"),
            "status": target_status,
            "metrics": metrics,
        }

        # Keep existing started_at / ended_at if not explicitly overridden
        if started_at:
            update_kwargs["started_at"] = started_at
        elif exp.get("started_at"):
            # Preserve existing started_at
            update_kwargs["started_at"] = exp["started_at"]

        if ended_at:
            update_kwargs["ended_at"] = ended_at
        elif exp.get("ended_at"):
            # Preserve existing ended_at
            update_kwargs["ended_at"] = exp["ended_at"]

        await self.repository.save_deployment_experiment(**update_kwargs)

        return CanaryOperationResult(
            experiment_id=experiment_id,
            operation=f"transition_to_{target_status}",
            previous_status=prev_status,
            new_status=target_status,
            success=True,
            message=f"Canary status changed from {prev_status} to {target_status} successfully.",
            warnings=[],
        )

    async def approve_canary(
        self, experiment_id: str, approved_by: str, approval_notes: str
    ) -> CanaryOperationResult:
        """Approve a planned canary rollout plan."""
        if not approval_notes or not approval_notes.strip():
            raise ValueError("Approval notes are required to approve a canary.")

        return await self._transition_status(
            experiment_id=experiment_id,
            actor=approved_by,
            reason=approval_notes,
            allowed_from=["planned"],
            target_status="approved",
            metrics_update={
                "approved_by": approved_by,
                "approval_notes": approval_notes,
            },
        )

    async def start_canary(self, experiment_id: str, started_by: str) -> CanaryOperationResult:
        """Start execution of an approved canary rollout."""
        return await self._transition_status(
            experiment_id=experiment_id,
            actor=started_by,
            reason="Canary started by manager",
            allowed_from=["approved", "paused"],
            target_status="running",
            started_at=datetime.now(timezone.utc),
        )

    async def pause_canary(
        self, experiment_id: str, paused_by: str, reason: str
    ) -> CanaryOperationResult:
        """Pause a currently running canary."""
        if not reason or not reason.strip():
            raise ValueError("A reason is required to pause a canary.")

        return await self._transition_status(
            experiment_id=experiment_id,
            actor=paused_by,
            reason=reason,
            allowed_from=["running"],
            target_status="paused",
        )

    async def complete_canary(
        self, experiment_id: str, completed_by: str, reason: str
    ) -> CanaryOperationResult:
        """Complete a canary and end the experiment successfully."""
        if not reason or not reason.strip():
            raise ValueError("A reason is required to complete a canary.")

        return await self._transition_status(
            experiment_id=experiment_id,
            actor=completed_by,
            reason=reason,
            allowed_from=["running"],
            target_status="completed",
            ended_at=datetime.now(timezone.utc),
        )

    async def rollback_canary(
        self, experiment_id: str, rolled_back_by: str, reason: str
    ) -> CanaryOperationResult:
        """Perform a safety rollback of a running or paused canary rollout."""
        if not reason or not reason.strip():
            raise ValueError("A rollback reason is required.")

        return await self._transition_status(
            experiment_id=experiment_id,
            actor=rolled_back_by,
            reason=reason,
            allowed_from=["running", "paused"],
            target_status="rolled_back",
            ended_at=datetime.now(timezone.utc),
            metrics_update={"rollback_reason": reason},
        )

    async def cancel_canary(
        self, experiment_id: str, cancelled_by: str, reason: str
    ) -> CanaryOperationResult:
        """Cancel a planned, approved, paused, or running canary rollout."""
        if not reason or not reason.strip():
            raise ValueError("A reason is required to cancel a canary.")

        # running -> cancelled is only allowed if reason is provided
        return await self._transition_status(
            experiment_id=experiment_id,
            actor=cancelled_by,
            reason=reason,
            allowed_from=["planned", "approved", "paused", "running"],
            target_status="cancelled",
            ended_at=datetime.now(timezone.utc),
        )

    async def get_canary(self, experiment_id: str) -> dict[str, Any]:
        """Fetch the rollout experiment details."""
        exp = await self.repository.get_deployment_experiment(experiment_id)
        if not exp:
            raise ValueError(f"Canary experiment '{experiment_id}' does not exist")
        return exp

    async def list_canaries(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List recent rollout experiments."""
        # Query all recent deployment experiments
        all_exps = await self.repository.list_recent_deployment_experiments(limit=100)
        if status:
            return [e for e in all_exps if e.get("status") == status][:limit]
        return all_exps[:limit]

    async def choose_prompt_for_call(
        self,
        prompt_name: str,
        call_id: str,
        experiment_id: str | None = None,
        force_control: bool = False,
        force_candidate: bool = False,
    ) -> CanaryDecision:
        """Decide deterministically which prompt version to route for this call."""
        # Check env flag for canary routing
        canary_enabled = os.environ.get("DANA_ENABLE_PROMPT_CANARY") == "true"

        # Resolve experiment
        running_exp = None
        if experiment_id:
            running_exp = await self.repository.get_deployment_experiment(experiment_id)
        else:
            # Query all running experiments
            all_running = await self.repository.query_deployment_experiments({"status": "running"})
            for e in all_running:
                met = e.get("metrics") or {}
                if met.get("prompt_name") == prompt_name:
                    running_exp = e
                    break

        if not running_exp:
            return CanaryDecision(
                use_candidate=False,
                prompt_version_id=None,
                reason="No running canary experiment found",
                bucket_value=0.0,
                traffic_percentage=0.0,
                prompt_name=prompt_name,
            )

        exp_id = running_exp["id"]
        exp_metrics = running_exp.get("metrics") or {}
        control_id = exp_metrics.get("control_prompt_version_id")
        candidate_id = running_exp.get("prompt_version_id")
        traffic_pct = running_exp.get("traffic_percent", 0.0)

        # Force Control Rule
        if force_control:
            return CanaryDecision(
                use_candidate=False,
                prompt_version_id=control_id,
                reason="Force control requested",
                bucket_value=0.0,
                traffic_percentage=traffic_pct,
                experiment_id=exp_id,
                prompt_name=prompt_name,
            )

        # Force Candidate Rule
        if force_candidate:
            allow_force_candidate = os.environ.get("DANA_ALLOW_FORCE_CANDIDATE_PROMPT") == "true"
            if allow_force_candidate:
                return CanaryDecision(
                    use_candidate=True,
                    prompt_version_id=candidate_id,
                    reason="Force candidate requested",
                    bucket_value=0.0,
                    traffic_percentage=traffic_pct,
                    experiment_id=exp_id,
                    prompt_name=prompt_name,
                )
            else:
                return CanaryDecision(
                    use_candidate=False,
                    prompt_version_id=control_id,
                    reason="Force candidate requested but disallowed by environment settings",
                    bucket_value=0.0,
                    traffic_percentage=traffic_pct,
                    experiment_id=exp_id,
                    prompt_name=prompt_name,
                )

        # Check status running
        if running_exp.get("status") != "running":
            return CanaryDecision(
                use_candidate=False,
                prompt_version_id=control_id,
                reason=f"Canary experiment exists but status is '{running_exp.get('status')}', not 'running'",
                bucket_value=0.0,
                traffic_percentage=traffic_pct,
                experiment_id=exp_id,
                prompt_name=prompt_name,
            )

        # Environment Gate Check
        if not canary_enabled:
            return CanaryDecision(
                use_candidate=False,
                prompt_version_id=control_id,
                reason="canary routing disabled by environment",
                bucket_value=0.0,
                traffic_percentage=traffic_pct,
                experiment_id=exp_id,
                prompt_name=prompt_name,
            )

        # Deterministic Bucketing
        bucket_key = f"{prompt_name}:{exp_id}:{call_id}"
        bucket_val = self.deterministic_bucket(bucket_key)

        if bucket_val < traffic_pct:
            return CanaryDecision(
                use_candidate=True,
                prompt_version_id=candidate_id,
                reason=f"Deterministic bucket {bucket_val:.2f}% is under traffic limit {traffic_pct}%",
                bucket_value=bucket_val,
                traffic_percentage=traffic_pct,
                experiment_id=exp_id,
                prompt_name=prompt_name,
            )
        else:
            return CanaryDecision(
                use_candidate=False,
                prompt_version_id=control_id,
                reason=f"Deterministic bucket {bucket_val:.2f}% is at or above traffic limit {traffic_pct}%",
                bucket_value=bucket_val,
                traffic_percentage=traffic_pct,
                experiment_id=exp_id,
                prompt_name=prompt_name,
            )

    async def generate_canary_report(
        self, experiment_id: str, output_dir: str | Path = "data/canary"
    ) -> tuple[str, str]:
        """Generate JSON and Markdown reports for the rollout experiment."""
        exp = await self.repository.get_deployment_experiment(experiment_id)
        if not exp:
            raise ValueError(f"Canary experiment '{experiment_id}' does not exist")

        metrics = exp.get("metrics") or {}
        prompt_name = metrics.get("prompt_name") or ""
        control_id = metrics.get("control_prompt_version_id") or ""
        candidate_id = exp.get("prompt_version_id") or ""
        traffic_pct = exp.get("traffic_percent", 0.0)

        # Extract gate result
        eligibility = metrics.get("eligibility_summary") or {}
        gate_summary = eligibility.get("gate_summary") or {}
        warnings = eligibility.get("warnings") or []

        # Started/Ended datetime mapping
        started_at = exp.get("started_at")
        ended_at = exp.get("ended_at")

        # Compile report model
        report = CanaryReport(
            experiment_id=experiment_id,
            experiment_name=exp.get("experiment_name") or "",
            prompt_name=prompt_name,
            status=exp.get("status") or "planned",
            control_prompt_version_id=control_id,
            candidate_prompt_version_id=candidate_id,
            traffic_percentage=traffic_pct,
            started_at=started_at.isoformat() if isinstance(started_at, datetime) else started_at,
            ended_at=ended_at.isoformat() if isinstance(ended_at, datetime) else ended_at,
            gate_summary=gate_summary,
            metrics_summary=metrics.get("metrics_summary") or metrics.get("metrics") or {},
            rollback_plan=metrics.get("rollback_plan") or {},
            warnings=warnings,
        )

        out_path = Path(output_dir)
        os.makedirs(out_path, exist_ok=True)

        json_path = out_path / f"canary_report_{experiment_id}.json"
        md_path = out_path / f"canary_report_{experiment_id}.md"

        # 1. Write JSON Report
        json_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

        # 2. Write Markdown Report
        audit_history = metrics.get("audit_history") or []
        audit_rows = []
        for a in audit_history:
            audit_rows.append(
                f"| {a.get('operation')} | {a.get('actor')} | {a.get('previous_status') or 'None'} | "
                f"{a.get('new_status')} | {a.get('reason')} | {a.get('timestamp')} |"
            )
        audit_table = "\n".join(audit_rows)

        rollback_plan = report.rollback_plan
        start_conds = "\n".join(f"- {c}" for c in metrics.get("start_conditions", []))
        stop_conds = "\n".join(f"- {c}" for c in metrics.get("stop_conditions", []))

        md_content = f"""# Dana Canary Rollout Report

Experiment: {report.experiment_name}
Prompt: {report.prompt_name}
Status: {report.status}
Generated at: {datetime.now(timezone.utc).isoformat()}

## Summary
- Control prompt version: {report.control_prompt_version_id or "N/A"}
- Candidate prompt version: {report.candidate_prompt_version_id}
- Traffic percentage: {report.traffic_percentage}%
- Max traffic percentage: {metrics.get("traffic_policy", {}).get("max_traffic_percentage", 10.0)}%
- Started at: {report.started_at or "N/A"}
- Ended at: {report.ended_at or "N/A"}

## Gate Summary
- Prompt validation: {"PASSED" if gate_summary.get("prompt_validation_passed") else "FAILED/SKIPPED"}
- Eval cases: {"PASSED" if gate_summary.get("eval_cases_passed") else "SKIPPED/FAILED"}
- Transcript replay: {"PASSED" if gate_summary.get("transcript_replay_passed") else "FAILED/SKIPPED"}
- Prospect simulations: {"PASSED" if gate_summary.get("prospect_simulations_passed") else "FAILED/SKIPPED"}

## Rollout Safety
### Start Conditions
{start_conds or "None"}

### Stop Conditions
{stop_conds or "None"}

### Rollback Plan
- Target status: {rollback_plan.get("target_status", "rolled_back")}
- Action: {rollback_plan.get("action", "rollback")}
- Route 100% to control: {str(rollback_plan.get("traffic_allocation", {}).get("control_percent") == 100.0)}
- Control version: {rollback_plan.get("traffic_allocation", {}).get("control_prompt_version_id", "N/A")}
- Preserve candidate record: {str(rollback_plan.get("preserve_candidate_record", True))}
- Require manager review: {str(rollback_plan.get("requires_manager_review", True))}

## Routing Policy
- Environment flag required: DANA_ENABLE_PROMPT_CANARY=true
- Deterministic bucketing: prompt_name + experiment_id + call_id
- Force-control behavior: Always route to control if force_control=True
- Force-candidate restrictions: Requires DANA_ALLOW_FORCE_CANDIDATE_PROMPT=true

## Audit History
| Operation | Actor | Previous Status | New Status | Reason | Timestamp |
| --- | --- | --- | --- | --- | --- |
{audit_table or "| None | | | | | |"}

## Recommended Next Actions
- Start only after approval
- Monitor QA/compliance daily
- Roll back on critical compliance failures
- Do not manually edit live prompt files
- Do not exceed traffic cap without new approval
"""
        md_path.write_text(md_content, encoding="utf-8")

        return str(json_path.resolve()), str(md_path.resolve())


class PromptResolver:
    """A safe prompt resolver that runtime can use to select prompt versions."""

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.manager = CanaryManager(repository=repository)

    async def resolve_prompt_version_id(
        self,
        prompt_name: str,
        call_id: str,
        force_control: bool = False,
        force_candidate: bool = False,
    ) -> Optional[str]:
        """Resolves the PromptVersion ID to use for the call.

        Defaults to None (control/default file prompt) if canary is disabled,
        errors occur, or the decision is control.
        Never throws exceptions (fails closed).
        """
        try:
            decision = await self.manager.choose_prompt_for_call(
                prompt_name=prompt_name,
                call_id=call_id,
                force_control=force_control,
                force_candidate=force_candidate,
            )
            if decision.use_candidate:
                return decision.prompt_version_id
        except Exception as e:
            # Log warning only, never crash
            import logging
            logging.getLogger(__name__).warning(
                "PromptResolver failed to resolve prompt version: %s", e
            )
        return None
