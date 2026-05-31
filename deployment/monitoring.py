"""Dana Canary Monitoring, Auto-Rollback, and Promotion Readiness System.

Implements automated monitoring of running canary prompt experiments against control,
safety signal detection, auto-rollback rules, and promotion readiness checking.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from storage.repository import Repository, parse_dt
from deployment.canary import CanaryManager


class CanaryMonitorConfig:
    """Configuration options for the canary monitoring process."""

    def __init__(
        self,
        experiment_id: str,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        min_candidate_calls: int = 25,
        min_control_calls: int = 25,
        max_critical_failures: int = 0,
        max_high_failures: int = 0,
        max_compliance_failure_rate: float = 0.0,
        max_transfer_before_consent_count: int = 0,
        max_dnc_failure_count: int = 0,
        max_wrong_number_failure_count: int = 0,
        max_price_quote_count: int = 0,
        max_licensed_claim_count: int = 0,
        max_human_claim_count: int = 0,
        min_candidate_qa_score: float = 0.90,
        max_qa_score_drop: float = 0.03,
        max_transfer_rate_drop: float = 0.10,
        max_hangup_rate_increase: float = 0.10,
        require_eval_gate_pass: bool = True,
        require_replay_gate_pass: bool = True,
        require_simulation_gate_pass: bool = True,
        auto_rollback: bool = False,
        output_dir: str | Path = "data/canary",
    ) -> None:
        self.experiment_id = experiment_id
        self.window_start = window_start
        self.window_end = window_end
        self.min_candidate_calls = min_candidate_calls
        self.min_control_calls = min_control_calls
        self.max_critical_failures = max_critical_failures
        self.max_high_failures = max_high_failures
        self.max_compliance_failure_rate = max_compliance_failure_rate
        self.max_transfer_before_consent_count = max_transfer_before_consent_count
        self.max_dnc_failure_count = max_dnc_failure_count
        self.max_wrong_number_failure_count = max_wrong_number_failure_count
        self.max_price_quote_count = max_price_quote_count
        self.max_licensed_claim_count = max_licensed_claim_count
        self.max_human_claim_count = max_human_claim_count
        self.min_candidate_qa_score = min_candidate_qa_score
        self.max_qa_score_drop = max_qa_score_drop
        self.max_transfer_rate_drop = max_transfer_rate_drop
        self.max_hangup_rate_increase = max_hangup_rate_increase
        self.require_eval_gate_pass = require_eval_gate_pass
        self.require_replay_gate_pass = require_replay_gate_pass
        self.require_simulation_gate_pass = require_simulation_gate_pass
        self.auto_rollback = auto_rollback
        self.output_dir = output_dir


class CanaryVariantMetrics:
    """Aggregated performance and compliance metrics for a specific rollout variant."""

    def __init__(
        self,
        variant: str,
        total_calls: int = 0,
        total_turns: int = 0,
        qa_reports: int = 0,
        tool_events: int = 0,
        transfers: int = 0,
        successful_transfers: int = 0,
        failed_transfers: int = 0,
        callbacks: int = 0,
        dnc_requests: int = 0,
        wrong_number_requests: int = 0,
        hangups: int = 0,
        average_qa_score: Optional[float] = None,
        average_compliance_score: Optional[float] = None,
        transfer_rate: Optional[float] = None,
        successful_transfer_rate: Optional[float] = None,
        failed_transfer_rate: Optional[float] = None,
        callback_rate: Optional[float] = None,
        hangup_rate: Optional[float] = None,
        compliance_failure_count: int = 0,
        critical_failure_count: int = 0,
        high_failure_count: int = 0,
        medium_failure_count: int = 0,
        low_failure_count: int = 0,
        transfer_before_consent_count: int = 0,
        dnc_failure_count: int = 0,
        wrong_number_failure_count: int = 0,
        price_quote_count: int = 0,
        licensed_claim_count: int = 0,
        human_claim_count: int = 0,
        you_qualify_count: int = 0,
        approval_claim_count: int = 0,
        sensitive_data_request_count: int = 0,
        tool_failure_count: int = 0,
        labels: Optional[dict[str, Any]] = None,
    ) -> None:
        self.variant = variant
        self.total_calls = total_calls
        self.total_turns = total_turns
        self.qa_reports = qa_reports
        self.tool_events = tool_events
        self.transfers = transfers
        self.successful_transfers = successful_transfers
        self.failed_transfers = failed_transfers
        self.callbacks = callbacks
        self.dnc_requests = dnc_requests
        self.wrong_number_requests = wrong_number_requests
        self.hangups = hangups
        self.average_qa_score = average_qa_score
        self.average_compliance_score = average_compliance_score
        self.transfer_rate = transfer_rate
        self.successful_transfer_rate = successful_transfer_rate
        self.failed_transfer_rate = failed_transfer_rate
        self.callback_rate = callback_rate
        self.hangup_rate = hangup_rate
        self.compliance_failure_count = compliance_failure_count
        self.critical_failure_count = critical_failure_count
        self.high_failure_count = high_failure_count
        self.medium_failure_count = medium_failure_count
        self.low_failure_count = low_failure_count
        self.transfer_before_consent_count = transfer_before_consent_count
        self.dnc_failure_count = dnc_failure_count
        self.wrong_number_failure_count = wrong_number_failure_count
        self.price_quote_count = price_quote_count
        self.licensed_claim_count = licensed_claim_count
        self.human_claim_count = human_claim_count
        self.you_qualify_count = you_qualify_count
        self.approval_claim_count = approval_claim_count
        self.sensitive_data_request_count = sensitive_data_request_count
        self.tool_failure_count = tool_failure_count
        self.labels = labels or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "total_calls": self.total_calls,
            "total_turns": self.total_turns,
            "qa_reports": self.qa_reports,
            "tool_events": self.tool_events,
            "transfers": self.transfers,
            "successful_transfers": self.successful_transfers,
            "failed_transfers": self.failed_transfers,
            "callbacks": self.callbacks,
            "dnc_requests": self.dnc_requests,
            "wrong_number_requests": self.wrong_number_requests,
            "hangups": self.hangups,
            "average_qa_score": self.average_qa_score,
            "average_compliance_score": self.average_compliance_score,
            "transfer_rate": self.transfer_rate,
            "successful_transfer_rate": self.successful_transfer_rate,
            "failed_transfer_rate": self.failed_transfer_rate,
            "callback_rate": self.callback_rate,
            "hangup_rate": self.hangup_rate,
            "compliance_failure_count": self.compliance_failure_count,
            "critical_failure_count": self.critical_failure_count,
            "high_failure_count": self.high_failure_count,
            "medium_failure_count": self.medium_failure_count,
            "low_failure_count": self.low_failure_count,
            "transfer_before_consent_count": self.transfer_before_consent_count,
            "dnc_failure_count": self.dnc_failure_count,
            "wrong_number_failure_count": self.wrong_number_failure_count,
            "price_quote_count": self.price_quote_count,
            "licensed_claim_count": self.licensed_claim_count,
            "human_claim_count": self.human_claim_count,
            "you_qualify_count": self.you_qualify_count,
            "approval_claim_count": self.approval_claim_count,
            "sensitive_data_request_count": self.sensitive_data_request_count,
            "tool_failure_count": self.tool_failure_count,
            "labels": self.labels,
        }


class CanarySafetySignal:
    """An alert signal indicating high risk, quality regression, or compliance failure."""

    def __init__(
        self,
        signal_type: str,
        severity: str,
        variant: str,
        count: int,
        rate: Optional[float] = None,
        threshold: Optional[float] = None,
        sample_call_ids: Optional[list[str]] = None,
        sample_turn_ids: Optional[list[str]] = None,
        message: str = "",
        recommended_action: str = "",
        rollback_required: bool = False,
    ) -> None:
        self.signal_type = signal_type
        self.severity = severity
        self.variant = variant
        self.count = count
        self.rate = rate
        self.threshold = threshold
        self.sample_call_ids = sample_call_ids or []
        self.sample_turn_ids = sample_turn_ids or []
        self.message = message
        self.recommended_action = recommended_action
        self.rollback_required = rollback_required

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "severity": self.severity,
            "variant": self.variant,
            "count": self.count,
            "rate": self.rate,
            "threshold": self.threshold,
            "sample_call_ids": self.sample_call_ids,
            "sample_turn_ids": self.sample_turn_ids,
            "message": self.message,
            "recommended_action": self.recommended_action,
            "rollback_required": self.rollback_required,
        }


class CanaryMonitoringResult:
    """The outcome of a complete monitoring run."""

    def __init__(
        self,
        experiment_id: str,
        experiment_name: str,
        prompt_name: str,
        status_before: str,
        status_after: str,
        monitored_at: str,
        control_metrics: CanaryVariantMetrics,
        candidate_metrics: CanaryVariantMetrics,
        unknown_metrics: CanaryVariantMetrics,
        safety_signals: list[CanarySafetySignal],
        rollback_triggered: bool,
        metrics_updated: bool,
        promotion_ready: bool,
        promotion_readiness: dict[str, Any],
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        rollback_reason: Optional[str] = None,
        report_json_path: Optional[str] = None,
        report_markdown_path: Optional[str] = None,
        warnings: Optional[list[str]] = None,
    ) -> None:
        self.experiment_id = experiment_id
        self.experiment_name = experiment_name
        self.prompt_name = prompt_name
        self.status_before = status_before
        self.status_after = status_after
        self.monitored_at = monitored_at
        self.window_start = window_start
        self.window_end = window_end
        self.control_metrics = control_metrics
        self.candidate_metrics = candidate_metrics
        self.unknown_metrics = unknown_metrics
        self.safety_signals = safety_signals
        self.rollback_triggered = rollback_triggered
        self.rollback_reason = rollback_reason
        self.promotion_ready = promotion_ready
        self.promotion_readiness = promotion_readiness
        self.metrics_updated = metrics_updated
        self.report_json_path = report_json_path
        self.report_markdown_path = report_markdown_path
        self.warnings = warnings or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "experiment_name": self.experiment_name,
            "prompt_name": self.prompt_name,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "monitored_at": self.monitored_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "control_metrics": self.control_metrics.to_dict(),
            "candidate_metrics": self.candidate_metrics.to_dict(),
            "unknown_metrics": self.unknown_metrics.to_dict(),
            "safety_signals": [s.to_dict() for s in self.safety_signals],
            "rollback_triggered": self.rollback_triggered,
            "rollback_reason": self.rollback_reason,
            "promotion_ready": self.promotion_ready,
            "promotion_readiness": self.promotion_readiness,
            "metrics_updated": self.metrics_updated,
            "report_json_path": self.report_json_path,
            "report_markdown_path": self.report_markdown_path,
            "warnings": self.warnings,
        }


class CanaryPromotionReadinessResult:
    """The outcome of a candidate's readiness evaluation for promotion."""

    def __init__(
        self,
        experiment_id: str,
        ready: bool,
        reasons: list[str],
        blockers: list[str],
        warnings: list[str],
        candidate_calls: int,
        control_calls: int,
        gate_summary: dict[str, Any],
        metric_summary: dict[str, Any],
        required_human_approval: bool = True,
        recommended_next_step: str = "",
    ) -> None:
        self.experiment_id = experiment_id
        self.ready = ready
        self.reasons = reasons
        self.blockers = blockers
        self.warnings = warnings
        self.candidate_calls = candidate_calls
        self.control_calls = control_calls
        self.gate_summary = gate_summary
        self.metric_summary = metric_summary
        self.required_human_approval = required_human_approval
        self.recommended_next_step = recommended_next_step

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "ready": self.ready,
            "reasons": self.reasons,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "candidate_calls": self.candidate_calls,
            "control_calls": self.control_calls,
            "gate_summary": self.gate_summary,
            "metric_summary": self.metric_summary,
            "required_human_approval": self.required_human_approval,
            "recommended_next_step": self.recommended_next_step,
        }


class CanaryMonitor:
    """Orchestrates canary monitoring runs, safety gates, and rollback alerts."""

    def __init__(
        self,
        repository: Repository | None = None,
        canary_manager: CanaryManager | None = None,
    ) -> None:
        self.repository = repository or Repository()
        self.canary_manager = canary_manager

    async def monitor_experiment(self, config: CanaryMonitorConfig) -> CanaryMonitoringResult:
        """Run a full monitoring cycle for a given experiment ID."""
        monitored_at = datetime.now(timezone.utc).isoformat()

        # Load experiment
        experiment = await self.load_experiment(config.experiment_id)
        status_before = experiment.get("status") or "planned"

        # Gather data
        data_bundle = await self.gather_canary_data(experiment, config)
        warnings = list(data_bundle.get("warnings") or [])

        # Split by variant
        split_data = self.split_by_variant(experiment, data_bundle)

        # Compute metrics
        control_metrics = self.compute_variant_metrics("control", split_data["control"])
        candidate_metrics = self.compute_variant_metrics("candidate", split_data["candidate"])
        unknown_metrics = self.compute_variant_metrics("unknown", split_data["unknown"])

        if unknown_metrics.total_calls > 0:
            warnings.append(f"Found {unknown_metrics.total_calls} calls with unknown variant attribution.")

        # Detect safety signals
        signals = self.detect_safety_signals(control_metrics, candidate_metrics, config)

        # Maybe rollback
        rollback_triggered, rollback_reason, status_after = await self.maybe_trigger_rollback(
            experiment, signals, config
        )

        # Reload experiment to avoid overwriting rollback changes
        fresh_exp = await self.repository.get_deployment_experiment(config.experiment_id)
        if fresh_exp:
            experiment = fresh_exp

        # Check promotion readiness
        readiness = await self.check_promotion_readiness(
            experiment, control_metrics, candidate_metrics, signals, config
        )

        # Compile result
        result = CanaryMonitoringResult(
            experiment_id=config.experiment_id,
            experiment_name=experiment.get("experiment_name") or "",
            prompt_name=experiment.get("metrics", {}).get("prompt_name") or "",
            status_before=status_before,
            status_after=status_after,
            monitored_at=monitored_at,
            control_metrics=control_metrics,
            candidate_metrics=candidate_metrics,
            unknown_metrics=unknown_metrics,
            safety_signals=signals,
            rollback_triggered=rollback_triggered,
            rollback_reason=rollback_reason,
            promotion_ready=readiness.ready,
            promotion_readiness=readiness.to_dict(),
            metrics_updated=False,
            window_start=config.window_start,
            window_end=config.window_end,
            warnings=warnings,
        )

        # Write reports
        json_p, md_p = self.write_monitoring_report(result, config.output_dir)
        result.report_json_path = json_p
        result.report_markdown_path = md_p

        # Update metrics in DB
        await self.update_experiment_metrics(experiment, result)
        result.metrics_updated = True

        return result

    async def load_experiment(self, experiment_id: str) -> dict[str, Any]:
        """Fetch the experiment record or raise ValueError."""
        exp = await self.repository.get_deployment_experiment(experiment_id)
        if not exp:
            raise ValueError(f"DeploymentExperiment with ID '{experiment_id}' does not exist.")
        return exp

    async def gather_canary_data(self, experiment: dict[str, Any], config: CanaryMonitorConfig) -> dict[str, list[dict[str, Any]]]:
        """Query storage tables and filter by experiment association and time window."""
        warnings = []
        candidate_version_id = experiment.get("prompt_version_id") or ""
        metrics = experiment.get("metrics") or {}
        control_version_id = metrics.get("control_prompt_version_id") or ""
        exp_id = experiment.get("id") or ""

        # Query all calls
        all_calls = []
        try:
            all_calls = await self.repository.query_calls({})
        except Exception as e:
            warnings.append(f"Failed to query calls: {e}")

        # Parse window datetimes
        w_start = parse_dt(config.window_start)
        w_end = parse_dt(config.window_end)

        # Filter calls belonging to this experiment/versions and within time window
        filtered_calls = []
        for c in all_calls:
            def get_val(key):
                if key in c:
                    return c[key]
                for dict_field in ["metadata", "compliance_flags", "qualification"]:
                    d = c.get(dict_field) or {}
                    if isinstance(d, dict) and key in d:
                        return d[key]
                return None

            pvid = get_val("prompt_version_id")
            c_exp_id = get_val("experiment_id")

            associated = (
                (pvid in [candidate_version_id, control_version_id]) or
                (c_exp_id == exp_id)
            )
            if not associated:
                continue

            call_time = parse_dt(c.get("started_at") or c.get("created_at"))
            if call_time:
                if w_start and call_time < w_start:
                    continue
                if w_end and call_time > w_end:
                    continue
            filtered_calls.append(c)

        call_ids = {c["call_id"] for c in filtered_calls}

        # Query other tables and filter by call_ids
        filtered_turns = []
        try:
            all_turns = await self.repository.query_call_turns({})
            filtered_turns = [t for t in all_turns if t.get("call_id") in call_ids]
        except Exception as e:
            warnings.append(f"Failed to query call turns: {e}")

        filtered_qa = []
        try:
            all_qa = await self.repository.query_qa_reports({})
            filtered_qa = [q for q in all_qa if q.get("call_id") in call_ids]
        except Exception as e:
            warnings.append(f"Failed to query QA reports: {e}")

        filtered_tools = []
        try:
            all_tools = await self.repository.query_tool_events({})
            filtered_tools = [t for t in all_tools if t.get("call_id") in call_ids]
        except Exception as e:
            warnings.append(f"Failed to query tool events: {e}")

        filtered_outcomes = []
        try:
            all_outcomes = await self.repository.query_call_outcome_labels({})
            filtered_outcomes = [o for o in all_outcomes if o.get("call_id") in call_ids]
        except Exception as e:
            warnings.append(f"Failed to query call outcome labels: {e}")

        return {
            "calls": filtered_calls,
            "call_turns": filtered_turns,
            "qa_reports": filtered_qa,
            "tool_events": filtered_tools,
            "call_outcome_labels": filtered_outcomes,
            "warnings": warnings,
        }

    def _resolve_call_variant(self, call: dict[str, Any], candidate_version_id: str, control_version_id: str) -> str:
        """Inspect various metadata fields to resolve variant attribution."""
        def get_val(key):
            if key in call:
                return call[key]
            for dict_field in ["metadata", "compliance_flags", "qualification"]:
                d = call.get(dict_field) or {}
                if isinstance(d, dict) and key in d:
                    return d[key]
            return None

        # Check prompt_version_id
        pvid = get_val("prompt_version_id")
        if pvid:
            if pvid == candidate_version_id:
                return "candidate"
            if pvid == control_version_id:
                return "control"

        # Check canary_variant / prompt_variant
        variant = get_val("canary_variant") or get_val("prompt_variant")
        if variant == "candidate":
            return "candidate"
        if variant == "control":
            return "control"

        # Check use_candidate
        use_cand = get_val("use_candidate")
        if use_cand is not None:
            if use_cand is True or str(use_cand).lower() == "true":
                return "candidate"
            if use_cand is False or str(use_cand).lower() == "false":
                return "control"

        return "unknown"

    def split_by_variant(self, experiment: dict[str, Any], data_bundle: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Split data bundle into control, candidate, and unknown subsets."""
        candidate_version_id = experiment.get("prompt_version_id") or ""
        metrics = experiment.get("metrics") or {}
        control_version_id = metrics.get("control_prompt_version_id") or ""

        # Map call_id -> variant
        call_variant_map = {}
        calls = data_bundle.get("calls") or []
        for call in calls:
            var = self._resolve_call_variant(call, candidate_version_id, control_version_id)
            call_variant_map[call["call_id"]] = var

        # Initialize split dict
        split = {
            "control": {
                "calls": [], "call_turns": [], "qa_reports": [], "tool_events": [], "call_outcome_labels": []
            },
            "candidate": {
                "calls": [], "call_turns": [], "qa_reports": [], "tool_events": [], "call_outcome_labels": []
            },
            "unknown": {
                "calls": [], "call_turns": [], "qa_reports": [], "tool_events": [], "call_outcome_labels": []
            }
        }

        # Populate calls
        for call in calls:
            var = call_variant_map[call["call_id"]]
            split[var]["calls"].append(call)

        # Populate other collections
        for collection_name in ["call_turns", "qa_reports", "tool_events", "call_outcome_labels"]:
            records = data_bundle.get(collection_name) or []
            for r in records:
                cid = r.get("call_id")
                var = call_variant_map.get(cid, "unknown")
                split[var][collection_name].append(r)

        return split

    def compute_variant_metrics(self, variant: str, data: dict[str, list[dict[str, Any]]]) -> CanaryVariantMetrics:
        """Compute aggregated metrics deterministically for a single variant partition."""
        total_calls = len(data.get("calls") or [])
        total_turns = len(data.get("call_turns") or [])
        qa_reports_count = len(data.get("qa_reports") or [])
        tool_events_count = len(data.get("tool_events") or [])

        transfers = 0
        successful_transfers = 0
        failed_transfers = 0
        callbacks = 0
        dnc_requests = 0
        wrong_number_requests = 0
        hangups = 0

        qa_scores = []
        compliance_scores = []

        # Parse calls outcome/score
        for call in data.get("calls") or []:
            outcome = (call.get("outcome") or "").lower()
            if outcome == "transfer":
                transfers += 1
            elif outcome == "callback":
                callbacks += 1
            elif outcome == "dnc":
                dnc_requests += 1
            elif outcome == "wrong_number":
                wrong_number_requests += 1
            elif outcome == "hangup":
                hangups += 1

            if call.get("qa_score") is not None:
                qa_scores.append(float(call["qa_score"]))

        # Read QA reports
        for qr in data.get("qa_reports") or []:
            scores = qr.get("scores") or {}
            qa_s = scores.get("qa_score") or scores.get("overall_score")
            if qa_s is not None:
                qa_scores.append(float(qa_s))
            else:
                meta = scores.get("metadata") or {}
                if isinstance(meta, dict) and meta.get("qa_score") is not None:
                    qa_scores.append(float(meta["qa_score"]))

            comp_s = scores.get("compliance_score")
            if comp_s is not None:
                compliance_scores.append(float(comp_s))

        # Read outcome labels
        for lbl in data.get("call_outcome_labels") or []:
            outcome = (lbl.get("outcome") or "").lower()
            if outcome == "transfer":
                transfers += 1
            elif outcome == "callback":
                callbacks += 1
            elif outcome == "dnc":
                dnc_requests += 1
            elif outcome == "wrong_number":
                wrong_number_requests += 1
            elif outcome == "hangup":
                hangups += 1

            t_success = lbl.get("transfer_success")
            if t_success is not None:
                if t_success:
                    successful_transfers += 1
                else:
                    failed_transfers += 1

        # Check tool events for transfers success / errors
        for te in data.get("tool_events") or []:
            tool_name = te.get("tool_name") or ""
            if tool_name == "transfer":
                res = te.get("result")
                if res is True or (isinstance(res, dict) and res.get("success") is True):
                    successful_transfers += 1
                else:
                    failed_transfers += 1

        # Scan for safety issues
        transfer_before_consent_count = 0
        dnc_failure_count = 0
        wrong_number_failure_count = 0
        price_quote_count = 0
        licensed_claim_count = 0
        human_claim_count = 0
        you_qualify_count = 0
        approval_claim_count = 0
        sensitive_data_request_count = 0
        tool_failure_count = 0

        critical_failure_count = 0
        high_failure_count = 0
        medium_failure_count = 0
        low_failure_count = 0
        compliance_failure_count = 0

        def has_failure(item: Any, pattern: str) -> bool:
            if not item:
                return False
            # Look inside dict keys/values or lists
            if isinstance(item, list):
                return any(has_failure(sub, pattern) for sub in item)
            if isinstance(item, dict):
                # Search keys and values recursively
                for k, v in item.items():
                    if pattern.lower() in str(k).lower():
                        return True
                    if has_failure(v, pattern):
                        return True
                return False
            item_str = str(item).lower()
            return pattern.lower() in item_str

        all_records = (
            (data.get("calls") or []) +
            (data.get("qa_reports") or []) +
            (data.get("tool_events") or []) +
            (data.get("call_outcome_labels") or [])
        )

        for r in all_records:
            # Check compliance_failure/qa_hard_fail flags
            if r.get("compliance_failure") is True or r.get("qa_hard_fail") is True:
                compliance_failure_count += 1

            # Check compliance_risk
            risk = r.get("compliance_risk") or (r.get("labels") or {}).get("compliance_risk") or (r.get("metadata") or {}).get("compliance_risk")
            if risk in ["high", "critical"]:
                compliance_failure_count += 1

            # Check individual failure rules
            # 1. transfer_before_consent
            if has_failure(r, "transfer_before_consent") or has_failure(r, "transfer before consent"):
                transfer_before_consent_count += 1
                critical_failure_count += 1
                compliance_failure_count += 1

            # 2. dnc_failure (continued_talking_after_dnc, dnc_requested_no_tool)
            if has_failure(r, "continued_talking_after_dnc") or has_failure(r, "dnc_requested_no_tool"):
                dnc_failure_count += 1
                critical_failure_count += 1
                compliance_failure_count += 1

            # 3. wrong_number_failure (continued_talking_after_wrong_number, wrong_number_no_close)
            if has_failure(r, "continued_talking_after_wrong_number") or has_failure(r, "wrong_number_no_close"):
                wrong_number_failure_count += 1
                critical_failure_count += 1
                compliance_failure_count += 1

            # 4. price_quote (agent_price_quote, price_quote)
            if has_failure(r, "agent_price_quote") or has_failure(r, "price_quote") or has_failure(r, "price quote"):
                price_quote_count += 1
                critical_failure_count += 1
                compliance_failure_count += 1

            # 5. licensed_claim (agent_claims_licensed, licensed claim)
            if has_failure(r, "agent_claims_licensed") or has_failure(r, "licensed claim"):
                licensed_claim_count += 1
                critical_failure_count += 1
                compliance_failure_count += 1

            # 6. human_claim (agent_claims_human, human claim)
            if has_failure(r, "agent_claims_human") or has_failure(r, "human claim"):
                human_claim_count += 1
                critical_failure_count += 1
                compliance_failure_count += 1

            # 7. you_qualify
            if has_failure(r, "agent_says_you_qualify") or has_failure(r, "you qualify") or has_failure(r, "you_qualify"):
                you_qualify_count += 1
                critical_failure_count += 1
                compliance_failure_count += 1

            # 8. approval_claim
            if has_failure(r, "approval_claim") or has_failure(r, "promise approval") or has_failure(r, "guaranteed approval"):
                approval_claim_count += 1
                critical_failure_count += 1
                compliance_failure_count += 1

            # 9. sensitive_data_request
            if has_failure(r, "sensitive_data_request") or has_failure(r, "requested_sensitive_data") or has_failure(r, "sensitive data request"):
                sensitive_data_request_count += 1
                critical_failure_count += 1
                compliance_failure_count += 1

            # High failures:
            # - repeated_push_after_not_interested
            # - missed_callback
            # - failed_transfer_tool
            # - missed_transfer_after_consent
            # - tool_failure
            # - QA hard fail
            high_indicators = [
                "repeated_push_after_not_interested",
                "missed_callback",
                "failed_transfer_tool",
                "missed_transfer_after_consent",
                "tool_failure",
                "qa_hard_fail",
            ]
            for ind in high_indicators:
                if has_failure(r, ind):
                    high_failure_count += 1
                    if ind == "tool_failure":
                        tool_failure_count += 1

            # Medium/low:
            # - multiple_questions
            # - response_too_long
            # - weak_objection_handling
            # - confusion_after_agent_response
            medium_indicators = [
                "multiple_questions",
                "response_too_long",
                "weak_objection_handling",
                "confusion_after_agent_response",
            ]
            for ind in medium_indicators:
                if has_failure(r, ind):
                    medium_failure_count += 1

        if compliance_failure_count < critical_failure_count:
            compliance_failure_count = critical_failure_count

        # Summarize averages
        average_qa_score = sum(qa_scores) / len(qa_scores) if qa_scores else None
        average_compliance_score = sum(compliance_scores) / len(compliance_scores) if compliance_scores else None

        transfer_rate = transfers / total_calls if total_calls > 0 else None
        successful_transfer_rate = successful_transfers / total_calls if total_calls > 0 else None
        failed_transfer_rate = failed_transfers / total_calls if total_calls > 0 else None
        callback_rate = callbacks / total_calls if total_calls > 0 else None
        hangup_rate = hangups / total_calls if total_calls > 0 else None

        return CanaryVariantMetrics(
            variant=variant,
            total_calls=total_calls,
            total_turns=total_turns,
            qa_reports=qa_reports_count,
            tool_events=tool_events_count,
            transfers=transfers,
            successful_transfers=successful_transfers,
            failed_transfers=failed_transfers,
            callbacks=callbacks,
            dnc_requests=dnc_requests,
            wrong_number_requests=wrong_number_requests,
            hangups=hangups,
            average_qa_score=average_qa_score,
            average_compliance_score=average_compliance_score,
            transfer_rate=transfer_rate,
            successful_transfer_rate=successful_transfer_rate,
            failed_transfer_rate=failed_transfer_rate,
            callback_rate=callback_rate,
            hangup_rate=hangup_rate,
            compliance_failure_count=compliance_failure_count,
            critical_failure_count=critical_failure_count,
            high_failure_count=high_failure_count,
            medium_failure_count=medium_failure_count,
            low_failure_count=low_failure_count,
            transfer_before_consent_count=transfer_before_consent_count,
            dnc_failure_count=dnc_failure_count,
            wrong_number_failure_count=wrong_number_failure_count,
            price_quote_count=price_quote_count,
            licensed_claim_count=licensed_claim_count,
            human_claim_count=human_claim_count,
            you_qualify_count=you_qualify_count,
            approval_claim_count=approval_claim_count,
            sensitive_data_request_count=sensitive_data_request_count,
            tool_failure_count=tool_failure_count,
        )

    def detect_safety_signals(
        self,
        control_metrics: CanaryVariantMetrics,
        candidate_metrics: CanaryVariantMetrics,
        config: CanaryMonitorConfig,
    ) -> list[CanarySafetySignal]:
        """Detect automated alert signals from computed metrics."""
        signals = []

        def add_signal(signal_type, severity, count, rate=None, threshold=None, message="", action="", rollback=False):
            signals.append(
                CanarySafetySignal(
                    signal_type=signal_type,
                    severity=severity,
                    variant="candidate",
                    count=count,
                    rate=rate,
                    threshold=threshold,
                    message=message,
                    recommended_action=action,
                    rollback_required=rollback,
                )
            )

        # 1. Critical failures
        if candidate_metrics.critical_failure_count > config.max_critical_failures:
            add_signal(
                signal_type="critical_failures_exceeded",
                severity="critical",
                count=candidate_metrics.critical_failure_count,
                threshold=float(config.max_critical_failures),
                message=f"Candidate critical failure count ({candidate_metrics.critical_failure_count}) exceeds threshold of {config.max_critical_failures}",
                action="Trigger immediate rollback",
                rollback=True,
            )

        # 2. High failures
        if candidate_metrics.high_failure_count > config.max_high_failures:
            add_signal(
                signal_type="high_failures_exceeded",
                severity="critical" if config.max_high_failures == 0 else "high",
                count=candidate_metrics.high_failure_count,
                threshold=float(config.max_high_failures),
                message=f"Candidate high failure count ({candidate_metrics.high_failure_count}) exceeds threshold of {config.max_high_failures}",
                action="Trigger immediate rollback" if config.max_high_failures == 0 else "Review candidate failures",
                rollback=(config.max_high_failures == 0),
            )

        # 3. transfer_before_consent
        if candidate_metrics.transfer_before_consent_count > config.max_transfer_before_consent_count:
            add_signal(
                signal_type="transfer_before_consent_limit_exceeded",
                severity="critical",
                count=candidate_metrics.transfer_before_consent_count,
                threshold=float(config.max_transfer_before_consent_count),
                message=f"Candidate transfer before consent count ({candidate_metrics.transfer_before_consent_count}) exceeds limit of {config.max_transfer_before_consent_count}",
                action="Trigger immediate rollback",
                rollback=True,
            )

        # 4. dnc_failure_count
        if candidate_metrics.dnc_failure_count > config.max_dnc_failure_count:
            add_signal(
                signal_type="dnc_failure_limit_exceeded",
                severity="critical",
                count=candidate_metrics.dnc_failure_count,
                threshold=float(config.max_dnc_failure_count),
                message=f"Candidate DNC failure count ({candidate_metrics.dnc_failure_count}) exceeds limit of {config.max_dnc_failure_count}",
                action="Trigger immediate rollback",
                rollback=True,
            )

        # 5. wrong_number_failure_count
        if candidate_metrics.wrong_number_failure_count > config.max_wrong_number_failure_count:
            add_signal(
                signal_type="wrong_number_failure_limit_exceeded",
                severity="critical",
                count=candidate_metrics.wrong_number_failure_count,
                threshold=float(config.max_wrong_number_failure_count),
                message=f"Candidate wrong number failure count ({candidate_metrics.wrong_number_failure_count}) exceeds limit of {config.max_wrong_number_failure_count}",
                action="Trigger immediate rollback",
                rollback=True,
            )

        # 6. price_quote_count
        if candidate_metrics.price_quote_count > config.max_price_quote_count:
            add_signal(
                signal_type="price_quote_limit_exceeded",
                severity="critical",
                count=candidate_metrics.price_quote_count,
                threshold=float(config.max_price_quote_count),
                message=f"Candidate price quote count ({candidate_metrics.price_quote_count}) exceeds limit of {config.max_price_quote_count}",
                action="Trigger immediate rollback",
                rollback=True,
            )

        # 7. licensed_claim_count
        if candidate_metrics.licensed_claim_count > config.max_licensed_claim_count:
            add_signal(
                signal_type="licensed_claim_limit_exceeded",
                severity="critical",
                count=candidate_metrics.licensed_claim_count,
                threshold=float(config.max_licensed_claim_count),
                message=f"Candidate licensed claim count ({candidate_metrics.licensed_claim_count}) exceeds limit of {config.max_licensed_claim_count}",
                action="Trigger immediate rollback",
                rollback=True,
            )

        # 8. human_claim_count
        if candidate_metrics.human_claim_count > config.max_human_claim_count:
            add_signal(
                signal_type="human_claim_limit_exceeded",
                severity="critical",
                count=candidate_metrics.human_claim_count,
                threshold=float(config.max_human_claim_count),
                message=f"Candidate human claim count ({candidate_metrics.human_claim_count}) exceeds limit of {config.max_human_claim_count}",
                action="Trigger immediate rollback",
                rollback=True,
            )

        # 9. you_qualify_count > 0
        if candidate_metrics.you_qualify_count > 0:
            add_signal(
                signal_type="you_qualify_failure",
                severity="critical",
                count=candidate_metrics.you_qualify_count,
                threshold=0.0,
                message=f"Candidate said 'you qualify' ({candidate_metrics.you_qualify_count} times)",
                action="Trigger immediate rollback",
                rollback=True,
            )

        # 10. approval_claim_count > 0
        if candidate_metrics.approval_claim_count > 0:
            add_signal(
                signal_type="approval_claim_failure",
                severity="critical",
                count=candidate_metrics.approval_claim_count,
                threshold=0.0,
                message=f"Candidate claimed approval / guaranteed acceptance ({candidate_metrics.approval_claim_count} times)",
                action="Trigger immediate rollback",
                rollback=True,
            )

        # 11. sensitive_data_request_count > 0
        if candidate_metrics.sensitive_data_request_count > 0:
            add_signal(
                signal_type="sensitive_data_request_failure",
                severity="critical",
                count=candidate_metrics.sensitive_data_request_count,
                threshold=0.0,
                message=f"Candidate requested sensitive data ({candidate_metrics.sensitive_data_request_count} times)",
                action="Trigger immediate rollback",
                rollback=True,
            )

        # Compliance failure rate limit
        cand_fail_rate = (
            candidate_metrics.compliance_failure_count / candidate_metrics.total_calls
            if candidate_metrics.total_calls > 0
            else 0.0
        )
        if cand_fail_rate > config.max_compliance_failure_rate:
            add_signal(
                signal_type="compliance_failure_rate_exceeded",
                severity="critical" if config.max_compliance_failure_rate == 0.0 else "high",
                count=candidate_metrics.compliance_failure_count,
                rate=cand_fail_rate,
                threshold=config.max_compliance_failure_rate,
                message=f"Candidate compliance failure rate ({cand_fail_rate:.2%}) exceeds limit of {config.max_compliance_failure_rate:.2%}",
                action="Trigger immediate rollback" if config.max_compliance_failure_rate == 0.0 else "Review compliance logs",
                rollback=(config.max_compliance_failure_rate == 0.0),
            )

        # Regression signals:
        # QA score below minimum
        if candidate_metrics.average_qa_score is not None and candidate_metrics.average_qa_score < config.min_candidate_qa_score:
            add_signal(
                signal_type="qa_score_below_minimum",
                severity="high",
                count=1,
                rate=candidate_metrics.average_qa_score,
                threshold=config.min_candidate_qa_score,
                message=f"Candidate average QA score ({candidate_metrics.average_qa_score:.2f}) is below minimum {config.min_candidate_qa_score:.2f}",
                action="Review prompt quality issues",
                rollback=False,
            )

        # QA score drops compared to control
        if candidate_metrics.average_qa_score is not None and control_metrics.average_qa_score is not None:
            qa_drop = control_metrics.average_qa_score - candidate_metrics.average_qa_score
            if qa_drop > config.max_qa_score_drop:
                add_signal(
                    signal_type="qa_score_drop_regression",
                    severity="high",
                    count=1,
                    rate=qa_drop,
                    threshold=config.max_qa_score_drop,
                    message=f"Candidate QA score drop ({qa_drop:.3f}) compared to control ({control_metrics.average_qa_score:.2f}) exceeds threshold of {config.max_qa_score_drop:.3f}",
                    action="Review candidate-vs-control prompts",
                    rollback=False,
                )

        # Transfer rate drops compared to control
        if candidate_metrics.transfer_rate is not None and control_metrics.transfer_rate is not None:
            transfer_drop = control_metrics.transfer_rate - candidate_metrics.transfer_rate
            if transfer_drop > config.max_transfer_rate_drop:
                add_signal(
                    signal_type="transfer_rate_drop_regression",
                    severity="high",
                    count=1,
                    rate=transfer_drop,
                    threshold=config.max_transfer_rate_drop,
                    message=f"Candidate transfer rate drop ({transfer_drop:.2%}) compared to control ({control_metrics.transfer_rate:.2%}) exceeds limit of {config.max_transfer_rate_drop:.2%}",
                    action="Review candidate routing and objection response quality",
                    rollback=False,
                )

        # Hangup rate increases compared to control
        if candidate_metrics.hangup_rate is not None and control_metrics.hangup_rate is not None:
            hangup_increase = candidate_metrics.hangup_rate - control_metrics.hangup_rate
            if hangup_increase > config.max_hangup_rate_increase:
                add_signal(
                    signal_type="hangup_rate_increase_regression",
                    severity="high",
                    count=1,
                    rate=hangup_increase,
                    threshold=config.max_hangup_rate_increase,
                    message=f"Candidate hangup rate increase ({hangup_increase:.2%}) compared to control ({control_metrics.hangup_rate:.2%}) exceeds limit of {config.max_hangup_rate_increase:.2%}",
                    action="Review call opening and pacing of candidate prompts",
                    rollback=False,
                )

        # Failed transfer rate drop
        if candidate_metrics.failed_transfer_rate is not None and control_metrics.failed_transfer_rate is not None:
            if candidate_metrics.failed_transfer_rate > control_metrics.failed_transfer_rate:
                add_signal(
                    signal_type="failed_transfer_rate_regression",
                    severity="high",
                    count=1,
                    rate=candidate_metrics.failed_transfer_rate - control_metrics.failed_transfer_rate,
                    message=f"Candidate failed transfer rate ({candidate_metrics.failed_transfer_rate:.2%}) is higher than control ({control_metrics.failed_transfer_rate:.2%})",
                    action="Review transfer tool integrations",
                    rollback=False,
                )

        # Insufficient data
        if candidate_metrics.total_calls < config.min_candidate_calls:
            add_signal(
                signal_type="insufficient_candidate_calls",
                severity="medium",
                count=candidate_metrics.total_calls,
                threshold=float(config.min_candidate_calls),
                message=f"Candidate calls ({candidate_metrics.total_calls}) are below minimum required ({config.min_candidate_calls})",
                action="Continue running experiment to gather more data",
                rollback=False,
            )

        if control_metrics.total_calls < config.min_control_calls:
            add_signal(
                signal_type="insufficient_control_calls",
                severity="medium",
                count=control_metrics.total_calls,
                threshold=float(config.min_control_calls),
                message=f"Control calls ({control_metrics.total_calls}) are below minimum required ({config.min_control_calls})",
                action="Continue running experiment to gather more control data",
                rollback=False,
            )

        return signals

    async def maybe_trigger_rollback(
        self, experiment: dict[str, Any], signals: list[CanarySafetySignal], config: CanaryMonitorConfig
    ) -> tuple[bool, Optional[str], str]:
        """Evaluate if safety rollback is required and trigger via CanaryManager if configured."""
        rollback_required = any(s.rollback_required for s in signals)
        if not rollback_required:
            return False, None, experiment.get("status") or "planned"

        current_status = experiment.get("status") or "planned"
        if current_status not in ["running", "paused"]:
            return False, f"Rollback recommended but experiment is already in status '{current_status}'", current_status

        # Summarize reason
        critical_msgs = [s.message for s in signals if s.rollback_required]
        reason = "Canary Safety Rollback: " + "; ".join(critical_msgs)

        if not config.auto_rollback:
            return False, f"Rollback recommended but not executed (auto_rollback=False). Reason: {reason}", current_status

        if self.canary_manager is None:
            self.canary_manager = CanaryManager(repository=self.repository)

        try:
            res = await self.canary_manager.rollback_canary(
                experiment_id=experiment["id"],
                rolled_back_by="CanaryMonitor",
                reason=reason,
            )
            if res.success:
                return True, reason, "rolled_back"
            else:
                return False, f"CanaryManager rollback failed: {res.message}", current_status
        except Exception as e:
            return False, f"CanaryManager rollback raised exception: {e}", current_status

    async def check_promotion_readiness(
        self,
        experiment: dict[str, Any],
        control_metrics: CanaryVariantMetrics,
        candidate_metrics: CanaryVariantMetrics,
        signals: list[CanarySafetySignal],
        config: CanaryMonitorConfig,
    ) -> CanaryPromotionReadinessResult:
        """Check all criteria to determine if candidate is ready for future manual promotion."""
        blockers = []
        warnings = []
        gate_result_passed = False

        # 1. Experiment status
        current_status = experiment.get("status") or "planned"
        if current_status not in ["running", "completed"]:
            blockers.append(f"Experiment status is '{current_status}', must be 'running' or 'completed'.")

        # 2. Candidate calls
        if candidate_metrics.total_calls < config.min_candidate_calls:
            blockers.append(f"Candidate calls ({candidate_metrics.total_calls}) are below minimum required ({config.min_candidate_calls}).")

        # 3. Control calls
        if control_metrics.total_calls < config.min_control_calls:
            msg = f"Control calls ({control_metrics.total_calls}) are below minimum required ({config.min_control_calls})."
            if control_metrics.total_calls == 0:
                warnings.append("Control sample is completely unavailable (0 calls).")
            else:
                blockers.append(msg)

        # 4. Critical and high safety signals
        critical_signals = [s for s in signals if s.severity == "critical"]
        if critical_signals:
            blockers.append(f"Critical safety signals detected: {[s.signal_type for s in critical_signals]}")

        high_signals = [s for s in signals if s.severity == "high"]
        if high_signals:
            blockers.append(f"High safety/regression signals detected: {[s.signal_type for s in high_signals]}")

        # 5. Zero-tolerance failure counts
        if candidate_metrics.critical_failure_count > 0:
            blockers.append(f"Candidate has {candidate_metrics.critical_failure_count} critical failures.")
        if candidate_metrics.high_failure_count > 0:
            blockers.append(f"Candidate has {candidate_metrics.high_failure_count} high failures.")
        if candidate_metrics.transfer_before_consent_count > 0:
            blockers.append("Candidate transfer before consent count must be 0.")
        if candidate_metrics.dnc_failure_count > 0:
            blockers.append("Candidate DNC failure count must be 0.")
        if candidate_metrics.wrong_number_failure_count > 0:
            blockers.append("Candidate wrong number failure count must be 0.")
        if candidate_metrics.price_quote_count > 0:
            blockers.append("Candidate price quote count must be 0.")
        if candidate_metrics.licensed_claim_count > 0:
            blockers.append("Candidate licensed claim count must be 0.")
        if candidate_metrics.human_claim_count > 0:
            blockers.append("Candidate human claim count must be 0.")
        if candidate_metrics.you_qualify_count > 0:
            blockers.append("Candidate you qualify count must be 0.")
        if candidate_metrics.approval_claim_count > 0:
            blockers.append("Candidate approval claim count must be 0.")
        if candidate_metrics.sensitive_data_request_count > 0:
            blockers.append("Candidate sensitive data request count must be 0.")

        # 6. QA minimum score
        if candidate_metrics.average_qa_score is not None and candidate_metrics.average_qa_score < config.min_candidate_qa_score:
            blockers.append(f"Candidate average QA score ({candidate_metrics.average_qa_score}) is below threshold of {config.min_candidate_qa_score}.")

        # 7. QA drop regression
        if candidate_metrics.average_qa_score is not None and control_metrics.average_qa_score is not None:
            qa_drop = control_metrics.average_qa_score - candidate_metrics.average_qa_score
            if qa_drop > config.max_qa_score_drop:
                blockers.append(f"Candidate QA score dropped by {qa_drop:.3f} compared to control, exceeding allowed drop of {config.max_qa_score_drop}.")

        # 8. Transfer rate drop regression
        if candidate_metrics.transfer_rate is not None and control_metrics.transfer_rate is not None:
            transfer_drop = control_metrics.transfer_rate - candidate_metrics.transfer_rate
            if transfer_drop > config.max_transfer_rate_drop:
                blockers.append(f"Candidate transfer rate dropped by {transfer_drop:.2%} compared to control, exceeding allowed drop of {config.max_transfer_rate_drop:.2%}.")

        # 9. Hangup rate increase regression
        if candidate_metrics.hangup_rate is not None and control_metrics.hangup_rate is not None:
            hangup_increase = candidate_metrics.hangup_rate - control_metrics.hangup_rate
            if hangup_increase > config.max_hangup_rate_increase:
                blockers.append(f"Candidate hangup rate increased by {hangup_increase:.2%} compared to control, exceeding allowed increase of {config.max_hangup_rate_increase:.2%}.")

        # 10. Rollback status check
        rollback_recommended = any(s.rollback_required for s in signals)
        if rollback_recommended:
            blockers.append("Rollback is recommended due to critical safety signals.")

        # 11. Retrieve candidate PromptVersion record to check Prompt 15 gate results
        pv_id = experiment.get("prompt_version_id")
        if pv_id:
            pv_rec = await self.repository.get_prompt_version(pv_id)
            if pv_rec:
                if self.canary_manager is None:
                    self.canary_manager = CanaryManager(repository=self.repository)
                meta = self.canary_manager._extract_candidate_metadata(pv_rec)
                gate_result = meta.get("gate_result") or {}
                if gate_result.get("passed") is True:
                    gate_result_passed = True
                else:
                    blockers.append("Prompt 15 validation/safety gate result check did not pass.")
            else:
                blockers.append(f"Candidate PromptVersion '{pv_id}' could not be loaded from database.")
        else:
            blockers.append("No candidate PromptVersion ID associated with this experiment.")

        # 12. Read recent eval/replay/simulation reports from disk
        eval_reports = self.read_json_reports([
            Path("data/evals"),
            Path("data/simulations"),
            Path("data/reports")
        ])

        for rep in eval_reports:
            ver_id = rep.get("prompt_version_id") or rep.get("candidate_prompt_version_id") or rep.get("version_id")
            exp_id = rep.get("experiment_id")

            if (ver_id and ver_id == pv_id) or (exp_id and exp_id == experiment.get("id")):
                passed_flag = rep.get("passed") or rep.get("success") or rep.get("eligible")
                if passed_flag is False:
                    blockers.append(f"Offline evaluation report failed: {rep.get('message') or rep.get('name') or 'unknown error'}")

        ready = len(blockers) == 0
        reasons_list = []
        if ready:
            reasons_list.append("All automated metric thresholds and safety checks have passed.")
            reasons_list.append("Candidate is eligible for manual manager review.")
            recommended_next_step = (
                "Human review and human approval required before any promotion. Proceed to future promotion workflow only after approval."
            )
        else:
            reasons_list.append(f"Promotion blocked by {len(blockers)} unmet criteria.")
            recommended_next_step = (
                "Candidate cannot be promoted. Resolve all blockers listed above before attempting promotion."
            )

        return CanaryPromotionReadinessResult(
            experiment_id=experiment["id"],
            ready=ready,
            reasons=reasons_list,
            blockers=blockers,
            warnings=warnings,
            candidate_calls=candidate_metrics.total_calls,
            control_calls=control_metrics.total_calls,
            gate_summary={
                "prompt_15_passed": gate_result_passed,
                "offline_reports_passed": all("Offline evaluation report failed" not in b for b in blockers)
            },
            metric_summary={
                "candidate_qa_score": candidate_metrics.average_qa_score,
                "control_qa_score": control_metrics.average_qa_score,
                "candidate_transfer_rate": candidate_metrics.transfer_rate,
                "control_transfer_rate": control_metrics.transfer_rate,
                "candidate_hangup_rate": candidate_metrics.hangup_rate,
                "control_hangup_rate": control_metrics.hangup_rate,
            },
            required_human_approval=True,
            recommended_next_step=recommended_next_step
        )

    def read_json_reports(self, directories: list[str | Path]) -> list[dict[str, Any]]:
        """Helper to scan given directories and read JSON report files safely, ignoring malformed JSON files."""
        reports = []
        for directory in directories:
            d = Path(directory)
            if not d.exists():
                continue
            for f in d.glob("*.json"):
                try:
                    reports.append(json.loads(f.read_text(encoding="utf-8")))
                except Exception:
                    pass
        return reports

    async def update_experiment_metrics(self, experiment: dict[str, Any], result: CanaryMonitoringResult) -> None:
        """Update metrics JSONB column in DeploymentExperiment."""
        fresh_exp = await self.repository.get_deployment_experiment(experiment["id"])
        if fresh_exp:
            experiment = fresh_exp

        metrics = experiment.get("metrics") or {}
        monitoring_history = metrics.get("monitoring_history") or []

        history_entry = {
            "monitored_at": result.monitored_at,
            "window_start": result.window_start,
            "window_end": result.window_end,
            "candidate_metrics": result.candidate_metrics.to_dict(),
            "control_metrics": result.control_metrics.to_dict(),
            "unknown_metrics": result.unknown_metrics.to_dict(),
            "safety_signals": [s.to_dict() for s in result.safety_signals],
            "rollback_triggered": result.rollback_triggered,
            "promotion_ready": result.promotion_ready
        }
        monitoring_history.append(history_entry)

        metrics["monitoring_history"] = monitoring_history
        metrics["latest_monitoring_result"] = result.to_dict()
        metrics["promotion_ready"] = result.promotion_ready
        metrics["promotion_readiness"] = result.promotion_readiness
        metrics["rollback_recommended"] = any(s.rollback_required for s in result.safety_signals)
        metrics["last_monitored_at"] = result.monitored_at

        await self.repository.save_deployment_experiment(
            id=experiment["id"],
            experiment_name=experiment.get("experiment_name"),
            prompt_version_id=experiment.get("prompt_version_id"),
            traffic_percent=experiment.get("traffic_percent"),
            status=result.status_after,
            metrics=metrics,
            started_at=experiment.get("started_at"),
            ended_at=experiment.get("ended_at")
        )

    def write_monitoring_report(self, result: CanaryMonitoringResult, output_dir: str | Path) -> tuple[str, str]:
        """Generate JSON and Markdown report files on disk."""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        exp_id = result.experiment_id

        json_path = out_path / f"canary_monitoring_{exp_id}_{timestamp}.json"
        md_path = out_path / f"canary_monitoring_{exp_id}_{timestamp}.md"

        json_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

        ctrl = result.control_metrics
        cand = result.candidate_metrics

        def diff_val(c_val, t_val, is_pct=False):
            if c_val is None or t_val is None:
                return "N/A"
            diff = t_val - c_val
            if is_pct:
                return f"{diff:+.2%}"
            return f"{diff:+.2f}"

        def fmt_val(val, is_pct=False):
            if val is None:
                return "N/A"
            if is_pct:
                return f"{val:.2%}"
            return f"{val:.2f}"

        def fmt_int(val):
            return str(val) if val is not None else "N/A"

        # Compare metrics table rows
        rows = [
            f"| QA Score | {fmt_val(ctrl.average_qa_score)} | {fmt_val(cand.average_qa_score)} | {diff_val(ctrl.average_qa_score, cand.average_qa_score)} | >= 0.90 | {'PASS' if cand.average_qa_score is not None and cand.average_qa_score >= 0.90 else 'FAIL'} |",
            f"| Compliance Score | {fmt_val(ctrl.average_compliance_score)} | {fmt_val(cand.average_compliance_score)} | {diff_val(ctrl.average_compliance_score, cand.average_compliance_score)} | N/A | N/A |",
            f"| Transfer Rate | {fmt_val(ctrl.transfer_rate, True)} | {fmt_val(cand.transfer_rate, True)} | {diff_val(ctrl.transfer_rate, cand.transfer_rate, True)} | N/A | N/A |",
            f"| Failed Transfer Rate | {fmt_val(ctrl.failed_transfer_rate, True)} | {fmt_val(cand.failed_transfer_rate, True)} | {diff_val(ctrl.failed_transfer_rate, cand.failed_transfer_rate, True)} | N/A | N/A |",
            f"| Callback Rate | {fmt_val(ctrl.callback_rate, True)} | {fmt_val(cand.callback_rate, True)} | {diff_val(ctrl.callback_rate, cand.callback_rate, True)} | N/A | N/A |",
            f"| Hangup Rate | {fmt_val(ctrl.hangup_rate, True)} | {fmt_val(cand.hangup_rate, True)} | {diff_val(ctrl.hangup_rate, cand.hangup_rate, True)} | N/A | N/A |",
            f"| Compliance Failures | {fmt_int(ctrl.compliance_failure_count)} | {fmt_int(cand.compliance_failure_count)} | {diff_val(ctrl.compliance_failure_count, cand.compliance_failure_count)} | <= 0 | {'PASS' if cand.compliance_failure_count == 0 else 'FAIL'} |",
            f"| Critical Failures | {fmt_int(ctrl.critical_failure_count)} | {fmt_int(cand.critical_failure_count)} | {diff_val(ctrl.critical_failure_count, cand.critical_failure_count)} | <= 0 | {'PASS' if cand.critical_failure_count == 0 else 'FAIL'} |",
            f"| High Failures | {fmt_int(ctrl.high_failure_count)} | {fmt_int(cand.high_failure_count)} | {diff_val(ctrl.high_failure_count, cand.high_failure_count)} | <= 0 | {'PASS' if cand.high_failure_count == 0 else 'FAIL'} |",
        ]
        metrics_table = "\n".join(rows)

        # Safety signals table
        sig_rows = []
        for s in result.safety_signals:
            sig_rows.append(
                f"| {s.severity} | {s.signal_type} | {s.variant} | {s.count} | {s.threshold or 'N/A'} | {s.rollback_required} | {s.recommended_action} |"
            )
        signals_table = "\n".join(sig_rows) if sig_rows else "| None | | | | | | |"

        # Blockers list
        blockers_str = "\n".join(f"- {b}" for b in result.promotion_readiness.get("blockers", [])) or "- None"
        warnings_str = "\n".join(f"- {w}" for w in result.warnings) or "- None"

        md_content = f"""# Dana Canary Monitoring Report

Experiment: {result.experiment_name}
Prompt: {result.prompt_name}
Status before: {result.status_before}
Status after: {result.status_after}
Monitored at: {result.monitored_at}
Window: {result.window_start or "Beginning"} to {result.window_end or "Now"}

## Executive Summary
- Candidate calls: {cand.total_calls}
- Control calls: {ctrl.total_calls}
- Critical failures: {cand.critical_failure_count}
- High failures: {cand.high_failure_count}
- Rollback triggered: {result.rollback_triggered}
- Rollback recommended: {any(s.rollback_required for s in result.safety_signals)}
- Promotion ready: {result.promotion_ready}

## Candidate vs Control Metrics
| metric | control | candidate | difference | threshold | status |
| --- | --- | --- | --- | --- | --- |
{metrics_table}

## Safety Signals
| severity | signal type | variant | count | threshold | rollback required | recommended action |
| --- | --- | --- | --- | --- | --- | --- |
{signals_table}

## Promotion Readiness
- Ready: {result.promotion_ready}
- Required human approval: {result.promotion_readiness.get('required_human_approval', True)}
- Recommended next step: {result.promotion_readiness.get('recommended_next_step', '')}

### Blockers
{blockers_str}

### Warnings
{warnings_str}

## Rollback Status
- Rollback triggered: {result.rollback_triggered}
- Rollback reason: {result.rollback_reason or "N/A"}
- Auto rollback enabled: {result.rollback_triggered or "N/A"}
- Current experiment status: {result.status_after}

## Data Quality
- Unknown attribution calls: {result.unknown_metrics.total_calls}
- Missing data sources: {", ".join(result.warnings) if result.warnings else "None"}
- Insufficient samples: {"Yes" if cand.total_calls < 25 or ctrl.total_calls < 25 else "No"}

## Required Next Actions
- If rollback triggered: review rollback report and inspect failures
- If rollback recommended: pause or rollback canary immediately
- If not promotion ready: continue monitoring or fix blockers
- If promotion ready: human approval required before future promotion
- Do not manually edit live prompt files
- Do not activate candidate without promotion workflow
"""
        md_path.write_text(md_content, encoding="utf-8")

        return str(json_path.resolve()), str(md_path.resolve())
