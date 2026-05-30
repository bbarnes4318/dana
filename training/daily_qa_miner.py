"""Dana's Daily QA Mining system.

Analyzes call logs, QA reports, turns, and outcomes to identify failures, Compliance
risks, winning response candidates, and writes daily markdown and JSON reports.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field

from storage.repository import Repository, parse_dt
from training.ingestion import redact_text
from safety.compliance_filter import ComplianceFilter
from core.objection_classifier import ObjectionClassifier

logger = logging.getLogger(__name__)


class FailureCluster(BaseModel):
    """Represents a grouped cluster of similar call failures."""

    cluster_id: str
    cluster_type: str
    stage: Optional[str] = None
    objection_type: Optional[str] = None
    severity: str
    count: int = 0
    sample_call_ids: list[str] = Field(default_factory=list)
    sample_turn_indices: list[int] = Field(default_factory=list)
    summary: str
    recommended_action: str
    labels: dict[str, Any] = Field(default_factory=dict)


class WinningResponseCandidate(BaseModel):
    """Represents a successful agent response turn candidate."""

    source_call_id: Optional[str] = None
    stage: str
    objection_type: Optional[str] = None
    user_text: str
    agent_response: str
    why_it_worked: str
    labels: dict[str, Any] = Field(default_factory=dict)
    recommended_use_for: list[str] = Field(default_factory=list)


class DailyQaMiningResult(BaseModel):
    """Result summary of running the daily QA miner."""

    date_from: str
    date_to: str
    total_calls_analyzed: int = 0
    total_turns_analyzed: int = 0
    total_qa_reports_analyzed: int = 0
    total_tool_events_analyzed: int = 0
    total_outcome_labels_analyzed: int = 0
    failure_clusters_created: int = 0
    winning_response_candidates_created: int = 0
    compliance_review_items_created: int = 0
    eval_case_candidates_created: int = 0
    training_example_candidates_created: int = 0
    human_review_items_created: int = 0
    skipped_items: int = 0
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    report_markdown_path: Optional[str] = None
    report_json_path: Optional[str] = None
    review_item_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    objection_frequency: dict[str, int] = Field(default_factory=dict)
    hangups_by_stage: dict[str, int] = Field(default_factory=dict)


def generate_payload_hash(
    item_type: str,
    call_id: str,
    turn_index: int,
    stage: str,
    objection_type: str,
    failure_or_candidate_type: str,
    user_text: str,
    agent_response: str,
    expected_behavior: str = "",
) -> str:
    """Generate a stable SHA-256 payload hash for review item deduplication."""
    parts = [
        item_type,
        call_id,
        str(turn_index),
        stage,
        objection_type,
        failure_or_candidate_type,
        user_text,
        agent_response,
        expected_behavior,
    ]
    raw_str = "|".join(parts)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


class DailyQaMiner:
    """Learning engine that processes daily conversation outcomes into coaching assets."""

    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository or Repository()
        self.compliance_filter = ComplianceFilter()
        try:
            self.objection_classifier = ObjectionClassifier()
        except Exception as e:
            logger.warning("Failed to initialize ObjectionClassifier: %s", e)
            self.objection_classifier = None

    def _get_timestamp(self, record: dict) -> Optional[datetime]:
        """Safely extract timestamp from record."""
        for field in ["created_at", "timestamp", "started_at", "ended_at", "call_started_at", "imported_at"]:
            if field in record and record[field]:
                val = record[field]
                dt = parse_dt(val)
                if dt:
                    return dt
        return None

    def _is_in_range(self, record: dict, date_from: str, date_to: str) -> bool:
        """Check if a record is within an inclusive date range (YYYY-MM-DD)."""
        dt = self._get_timestamp(record)
        if not dt:
            return False
        date_str = dt.strftime("%Y-%m-%d")
        return date_from <= date_str <= date_to

    async def mine_date(self, date_str: str, dry_run: bool = False) -> DailyQaMiningResult:
        """Analyze records for an exact single date."""
        return await self.mine_range(date_str, date_str, dry_run=dry_run)

    async def mine_range(self, date_from: str, date_to: str, dry_run: bool = False) -> DailyQaMiningResult:
        """Analyze records for an inclusive date range."""
        result = DailyQaMiningResult(date_from=date_from, date_to=date_to)

        calls = []
        call_turns = []
        qa_reports = []
        tool_events = []
        outcome_labels = []

        def has_method(name: str) -> bool:
            return hasattr(self.repository, name) and callable(getattr(self.repository, name))

        # 1. Calls
        try:
            if has_method("query_calls"):
                all_calls = await self.repository.query_calls({})
                for c in all_calls:
                    if self._is_in_range(c, date_from, date_to):
                        calls.append(c)
                    elif not self._get_timestamp(c):
                        result.skipped_items += 1
                        result.skipped_reasons["missing timestamp"] = result.skipped_reasons.get("missing timestamp", 0) + 1
            else:
                result.warnings.append("query_calls method missing from repository")
        except Exception as e:
            result.warnings.append(f"Failed to query calls: {e}")

        # 2. Turns
        try:
            if has_method("query_call_turns"):
                all_turns = await self.repository.query_call_turns({})
                for t in all_turns:
                    if self._is_in_range(t, date_from, date_to):
                        call_turns.append(t)
                    elif not self._get_timestamp(t):
                        result.skipped_items += 1
                        result.skipped_reasons["missing timestamp"] = result.skipped_reasons.get("missing timestamp", 0) + 1
            else:
                result.warnings.append("query_call_turns method missing from repository")
        except Exception as e:
            result.warnings.append(f"Failed to query call turns: {e}")

        # 3. QA Reports
        try:
            if has_method("query_qa_reports"):
                all_qa = await self.repository.query_qa_reports({})
                for q in all_qa:
                    if self._is_in_range(q, date_from, date_to):
                        qa_reports.append(q)
                    elif not self._get_timestamp(q):
                        result.skipped_items += 1
                        result.skipped_reasons["missing timestamp"] = result.skipped_reasons.get("missing timestamp", 0) + 1
            else:
                result.warnings.append("query_qa_reports method missing from repository")
        except Exception as e:
            result.warnings.append(f"Failed to query QA reports: {e}")

        # 4. Tool Events
        try:
            if has_method("query_tool_events"):
                all_tools = await self.repository.query_tool_events({})
                for te in all_tools:
                    if self._is_in_range(te, date_from, date_to):
                        tool_events.append(te)
                    elif not self._get_timestamp(te):
                        result.skipped_items += 1
                        result.skipped_reasons["missing timestamp"] = result.skipped_reasons.get("missing timestamp", 0) + 1
            else:
                result.warnings.append("query_tool_events method missing from repository")
        except Exception as e:
            result.warnings.append(f"Failed to query tool events: {e}")

        # 5. Outcome Labels
        try:
            if has_method("query_call_outcome_labels"):
                all_outcomes = await self.repository.query_call_outcome_labels({})
                for ol in all_outcomes:
                    if self._is_in_range(ol, date_from, date_to):
                        outcome_labels.append(ol)
                    elif not self._get_timestamp(ol):
                        result.skipped_items += 1
                        result.skipped_reasons["missing timestamp"] = result.skipped_reasons.get("missing timestamp", 0) + 1
            else:
                result.warnings.append("query_call_outcome_labels method missing from repository")
        except Exception as e:
            result.warnings.append(f"Failed to query call outcome labels: {e}")

        # Execute analysis
        analysis_result = await self.analyze_calls(
            calls=calls,
            call_turns=call_turns,
            qa_reports=qa_reports,
            tool_events=tool_events,
            outcome_labels=outcome_labels,
            dry_run=dry_run
        )

        # Merge summary metrics
        result.total_calls_analyzed = analysis_result.total_calls_analyzed
        result.total_turns_analyzed = analysis_result.total_turns_analyzed
        result.total_qa_reports_analyzed = analysis_result.total_qa_reports_analyzed
        result.total_tool_events_analyzed = analysis_result.total_tool_events_analyzed
        result.total_outcome_labels_analyzed = analysis_result.total_outcome_labels_analyzed

        result.failure_clusters_created = analysis_result.failure_clusters_created
        result.winning_response_candidates_created = analysis_result.winning_response_candidates_created
        result.compliance_review_items_created = analysis_result.compliance_review_items_created
        result.eval_case_candidates_created = analysis_result.eval_case_candidates_created
        result.training_example_candidates_created = analysis_result.training_example_candidates_created
        result.human_review_items_created = analysis_result.human_review_items_created

        result.skipped_items += analysis_result.skipped_items
        for k, v in analysis_result.skipped_reasons.items():
            result.skipped_reasons[k] = result.skipped_reasons.get(k, 0) + v

        result.review_item_ids = analysis_result.review_item_ids
        result.warnings.extend(analysis_result.warnings)
        result.objection_frequency = analysis_result.objection_frequency
        result.hangups_by_stage = analysis_result.hangups_by_stage

        # Generate output reports
        report_md_path, report_json_path = self.write_daily_report(result, analysis_result)
        result.report_markdown_path = report_md_path
        result.report_json_path = report_json_path

        return result

    async def analyze_calls(
        self,
        calls: list[dict],
        call_turns: list[dict],
        qa_reports: list[dict],
        tool_events: list[dict],
        outcome_labels: list[dict],
        dry_run: bool = False
    ) -> DailyQaMiningResult:
        """Inspect dialog turns and call events to locate failures and successes."""
        result = DailyQaMiningResult(
            date_from="",
            date_to="",
            total_calls_analyzed=len(calls),
            total_turns_analyzed=len(call_turns),
            total_qa_reports_analyzed=len(qa_reports),
            total_tool_events_analyzed=len(tool_events),
            total_outcome_labels_analyzed=len(outcome_labels)
        )

        # Group components by call_id
        turns_by_call: dict[str, list[dict]] = {}
        for t in call_turns:
            turns_by_call.setdefault(t.get("call_id", ""), []).append(t)

        for cid, t_list in turns_by_call.items():
            t_list.sort(key=lambda x: x.get("turn_number") or x.get("timestamp") or x.get("created_at") or 0)

        tools_by_call: dict[str, list[dict]] = {}
        for te in tool_events:
            tools_by_call.setdefault(te.get("call_id", ""), []).append(te)

        qa_by_call: dict[str, list[dict]] = {}
        for qr in qa_reports:
            qa_by_call.setdefault(qr.get("call_id", ""), []).append(qr)

        outcome_by_call: dict[str, list[dict]] = {}
        for ol in outcome_labels:
            outcome_by_call.setdefault(ol.get("call_id", ""), []).append(ol)

        all_call_ids = set(turns_by_call.keys())
        all_call_ids.update(tools_by_call.keys())
        all_call_ids.update(qa_by_call.keys())
        all_call_ids.update(outcome_by_call.keys())
        all_call_ids.update(c.get("call_id", "") for c in calls if c.get("call_id"))
        all_call_ids.discard("")

        failures: list[dict] = []
        winning_responses: list[WinningResponseCandidate] = []

        for cid in all_call_ids:
            turns = turns_by_call.get(cid, [])
            call_tools = tools_by_call.get(cid, [])
            call_qa = qa_by_call.get(cid, [])
            call_outcomes = outcome_by_call.get(cid, [])

            # Check QA overall scorecard failures
            for qr in call_qa:
                overall_score = qr.get("overall_score") or 0.0
                if overall_score < 7.0:
                    failures.append({
                        "call_id": cid,
                        "turn_index": -1,
                        "stage": "qa",
                        "objection_type": None,
                        "failure_type": "qa_hard_fail",
                        "severity": "high",
                        "user_text": "",
                        "agent_response": "",
                        "details": f"QA report failed with overall score of {overall_score}."
                    })

                scores = qr.get("scores") or {}
                compliance_score = scores.get("compliance_safety") or 10.0
                if compliance_score < 7.0:
                    failures.append({
                        "call_id": cid,
                        "turn_index": -1,
                        "stage": "compliance",
                        "objection_type": None,
                        "failure_type": "compliance_score_low",
                        "severity": "high",
                        "user_text": "",
                        "agent_response": "",
                        "details": f"Compliance QA score is below safe limit: {compliance_score}."
                    })

            # Check outcome-based metrics
            for ol in call_outcomes:
                transfer_quality = ol.get("transfer_quality_score")
                if transfer_quality is not None:
                    try:
                        tq = float(transfer_quality)
                        if tq < 5.0:
                            failures.append({
                                "call_id": cid,
                                "turn_index": -1,
                                "stage": "transfer",
                                "objection_type": None,
                                "failure_type": "transfer_quality_low",
                                "severity": "high",
                                "user_text": "",
                                "agent_response": "",
                                "details": f"Transfer quality score is too low: {tq}."
                            })
                    except ValueError:
                        pass

            # Check tool integration failures
            has_callback_event = any(te.get("tool_name") in ("schedule_callback", "callback") for te in call_tools)
            has_dnc_event = any(te.get("tool_name") in ("add_to_dnc", "dnc") for te in call_tools)

            for te in call_tools:
                if te.get("success") is False:
                    tool_name = te.get("tool_name")
                    failures.append({
                        "call_id": cid,
                        "turn_index": -1,
                        "stage": "tool",
                        "objection_type": None,
                        "failure_type": f"failed_{tool_name}_tool",
                        "severity": "high",
                        "user_text": "",
                        "agent_response": "",
                        "details": f"Tool '{tool_name}' failed execution."
                    })

            # Inspect dialog turns
            for idx, turn in enumerate(turns):
                speaker = turn.get("speaker")
                text = turn.get("text") or ""
                stage = turn.get("stage", "")
                text_lower = text.lower()

                if speaker == "prospect" or speaker == "user":
                    is_dnc = any(k in text_lower for k in ["stop calling", "remove me", "don't call", "take me off", "do not call", "remove from your list"])
                    is_wrong_number = any(k in text_lower for k in ["wrong number", "not the person", "incorrect number", "wrong person", "no john here", "no alex here"])
                    is_callback = any(k in text_lower for k in ["call me back", "call back", "busy", "callback", "tomorrow", "next week", "driving", "working"])

                    if is_dnc:
                        has_further_turns = any(t.get("speaker") in ("agent", "assistant") for t in turns[idx+1:])
                        if has_further_turns:
                            failures.append({
                                "call_id": cid,
                                "turn_index": idx,
                                "stage": stage,
                                "objection_type": "dnc",
                                "failure_type": "continued_talking_after_dnc",
                                "severity": "critical",
                                "user_text": text,
                                "agent_response": turns[idx+1].get("text") if idx+1 < len(turns) else "",
                                "details": "Prospect requested DNC but agent continued selling."
                            })
                        if not has_dnc_event:
                            failures.append({
                                "call_id": cid,
                                "turn_index": idx,
                                "stage": stage,
                                "objection_type": "dnc",
                                "failure_type": "dnc_requested_no_tool",
                                "severity": "high",
                                "user_text": text,
                                "agent_response": "",
                                "details": "DNC requested by prospect but no DNC tool event occurred."
                            })

                    if is_wrong_number:
                        has_further_turns = any(t.get("speaker") in ("agent", "assistant") for t in turns[idx+1:])
                        if has_further_turns:
                            failures.append({
                                "call_id": cid,
                                "turn_index": idx,
                                "stage": stage,
                                "objection_type": "wrong_number",
                                "failure_type": "continued_talking_after_wrong_number",
                                "severity": "critical",
                                "user_text": text,
                                "agent_response": turns[idx+1].get("text") if idx+1 < len(turns) else "",
                                "details": "Prospect stated wrong number but agent continued selling."
                            })
                        has_close_event = any(te.get("tool_name") in ("add_to_dnc", "close_call", "end_call", "hangup") for te in call_tools)
                        if not has_close_event:
                            failures.append({
                                "call_id": cid,
                                "turn_index": idx,
                                "stage": stage,
                                "objection_type": "wrong_number",
                                "failure_type": "wrong_number_no_close_event",
                                "severity": "high",
                                "user_text": text,
                                "agent_response": "",
                                "details": "Wrong number stated but no call close tool event occurred."
                            })

                    if is_callback:
                        if not has_callback_event:
                            failures.append({
                                "call_id": cid,
                                "turn_index": idx,
                                "stage": stage,
                                "objection_type": "callback",
                                "failure_type": "callback_requested_no_tool",
                                "severity": "medium",
                                "user_text": text,
                                "agent_response": "",
                                "details": "Prospect requested callback but scheduling tool was missed."
                            })

                elif speaker == "agent" or speaker == "assistant":
                    # Compliance checks
                    # 1. Price Quotes
                    price_pattern = re.compile(
                        r"\$\s?\d+(?:\.\d{2})?\s*(?:per|a|/)\s*(?:month|mo)|\$\s?\d+\b", re.IGNORECASE
                    )
                    if price_pattern.search(text):
                        failures.append({
                            "call_id": cid,
                            "turn_index": idx,
                            "stage": stage,
                            "objection_type": None,
                            "failure_type": "agent_price_quote",
                            "severity": "critical",
                            "user_text": turns[idx-1].get("text") if idx > 0 else "",
                            "agent_response": text,
                            "details": f"Agent quoted specific premium price: {text}"
                        })

                    # 2. You Qualify
                    if "you qualify" in text_lower or "you're qualified" in text_lower or "you are qualified" in text_lower:
                        failures.append({
                            "call_id": cid,
                            "turn_index": idx,
                            "stage": stage,
                            "objection_type": None,
                            "failure_type": "agent_you_qualify",
                            "severity": "critical",
                            "user_text": turns[idx-1].get("text") if idx > 0 else "",
                            "agent_response": text,
                            "details": "Agent said you qualify."
                        })

                    # 3. Claims Licensed
                    if "i'm licensed" in text_lower or "i am licensed" in text_lower or "i am a licensed agent" in text_lower or "my license" in text_lower or "licensed to" in text_lower:
                        failures.append({
                            "call_id": cid,
                            "turn_index": idx,
                            "stage": stage,
                            "objection_type": None,
                            "failure_type": "agent_claims_licensed",
                            "severity": "critical",
                            "user_text": turns[idx-1].get("text") if idx > 0 else "",
                            "agent_response": text,
                            "details": "Agent claimed to be licensed."
                        })

                    # 4. Claims Human
                    if "i'm a real person" in text_lower or "i am human" in text_lower or "not a bot" in text_lower or "not an ai" in text_lower or "i'm a human" in text_lower or "real person" in text_lower:
                        failures.append({
                            "call_id": cid,
                            "turn_index": idx,
                            "stage": stage,
                            "objection_type": None,
                            "failure_type": "agent_claims_human",
                            "severity": "critical",
                            "user_text": turns[idx-1].get("text") if idx > 0 else "",
                            "agent_response": text,
                            "details": "Agent claimed to be human/real person."
                        })

                    # 5. Multiple Questions
                    if text.count("?") > 1:
                        failures.append({
                            "call_id": cid,
                            "turn_index": idx,
                            "stage": stage,
                            "objection_type": None,
                            "failure_type": "multiple_questions",
                            "severity": "medium",
                            "user_text": turns[idx-1].get("text") if idx > 0 else "",
                            "agent_response": text,
                            "details": "Agent asked more than one question in a turn."
                        })

                    # 6. Transfer before consent
                    transfer_phrases = ["transferring you", "connecting you", "connect you", "transfer you", "let me get an agent", "hold on while I transfer", "hold on, one moment"]
                    if any(p in text_lower for p in transfer_phrases):
                        is_consent = False
                        if idx > 0:
                            prev_text = turns[idx-1].get("text", "").lower()
                            is_consent = any(w in prev_text for w in ["yes", "sure", "okay", "go ahead", "transfer", "connect"])
                        if not is_consent:
                            failures.append({
                                "call_id": cid,
                                "turn_index": idx,
                                "stage": stage,
                                "objection_type": None,
                                "failure_type": "transfer_before_consent",
                                "severity": "critical",
                                "user_text": turns[idx-1].get("text") if idx > 0 else "",
                                "agent_response": text,
                                "details": "Agent started transfer before explicit permission."
                            })

                    # Winning response check
                    if len(text) < 200 and text.count("?") <= 1:
                        comp_res = self.compliance_filter.check(text)
                        _, pii_count = redact_text(text)

                        if comp_res.is_safe and pii_count == 0:
                            next_prospect_positive = True
                            if idx + 1 < len(turns):
                                next_text = turns[idx+1].get("text", "").lower()
                                negative_words = ["stop", "dnc", "no", "not interested", "wrong number", "hang up", "take me off", "license", "scam"]
                                if any(w in next_text for w in negative_words):
                                    next_prospect_positive = False

                            if idx > 0 and next_prospect_positive:
                                prev_text = turns[idx-1].get("text", "").lower()
                                is_price_obj = "how much" in prev_text or "price" in prev_text or "cost" in prev_text
                                is_real_obj = "real person" in prev_text or "human" in prev_text or "are you real" in prev_text
                                is_licensed_obj = "licensed" in prev_text or "my license" in prev_text

                                if is_price_obj or is_real_obj or is_licensed_obj:
                                    obj_type = "price" if is_price_obj else ("are_you_real" if is_real_obj else "are_you_licensed")
                                    winning_responses.append(WinningResponseCandidate(
                                        source_call_id=cid,
                                        stage=stage,
                                        objection_type=obj_type,
                                        user_text=turns[idx-1].get("text") or "",
                                        agent_response=text,
                                        why_it_worked=f"Agent handled prospect objection '{obj_type}' safely.",
                                        labels={"compliance_risk": "none"},
                                        recommended_use_for=["prompt", "rag"]
                                    ))

            # Check hangup after agent turn
            if turns:
                last_turn = turns[-1]
                if last_turn.get("speaker") in ("agent", "assistant"):
                    is_hangup = True
                    for ol in call_outcomes:
                        if ol.get("outcome") == "transfer_successful":
                            is_hangup = False
                    if is_hangup:
                        failures.append({
                            "call_id": cid,
                            "turn_index": len(turns) - 1,
                            "stage": last_turn.get("stage", "unknown"),
                            "objection_type": None,
                            "failure_type": "hangup_after_agent_turn",
                            "severity": "medium",
                            "user_text": "",
                            "agent_response": last_turn.get("text") or "",
                            "details": "Prospect hung up immediately after agent turn."
                        })

        # Store analysis objects internally in result
        clusters = self.cluster_failures(failures)
        result.failure_clusters_created = len(clusters)
        result.winning_response_candidates_created = len(winning_responses)

        # Save reviews items
        item_ids, skipped, skipped_reasons = await self.create_review_items_from_analysis(
            failures, winning_responses, dry_run=dry_run
        )

        result.review_item_ids = item_ids
        result.skipped_items = skipped
        result.skipped_reasons = skipped_reasons
        result.human_review_items_created = len(item_ids)

        # Counts by review type
        c_count = 0
        f_count = 0
        e_count = 0
        t_count = len(winning_responses)
        
        for f in failures:
            ft = f["failure_type"]
            if ft in ("continued_talking_after_dnc", "dnc_requested_no_tool", "agent_price_quote"):
                c_count += 1
                f_count += 1
                e_count += 1
            elif ft in ("continued_talking_after_wrong_number", "transfer_before_consent", "wrong_number_no_close_event"):
                c_count += 1
                e_count += 1
            elif ft in ("agent_you_qualify", "agent_claims_licensed", "agent_claims_human"):
                c_count += 1
                e_count += 1
            elif ft == "multiple_questions":
                f_count += 1
                e_count += 1
            elif ft == "callback_requested_no_tool":
                e_count += 1
            elif ft == "hangup_after_agent_turn":
                f_count += 1
            else:
                sev = f.get("severity")
                if sev == "critical":
                    c_count += 1
                    e_count += 1
                elif sev in ("high", "medium"):
                    f_count += 1
                    e_count += 1
        
        # Adjust created counts based on actual saved results
        # If dry-run, we still report what would be created
        result.compliance_review_items_created = c_count
        result.eval_case_candidates_created = e_count
        result.training_example_candidates_created = t_count

        # Build objection stats dynamically
        obj_counts = {}
        for t in call_turns:
            obj = t.get("objection_type") or t.get("labels", {}).get("objection_type")
            if obj:
                obj_counts[obj] = obj_counts.get(obj, 0) + 1
        for f in failures:
            obj = f.get("objection_type")
            if obj:
                obj_counts[obj] = obj_counts.get(obj, 0) + 1
        for wr in winning_responses:
            obj = wr.objection_type
            if obj:
                obj_counts[obj] = obj_counts.get(obj, 0) + 1
        result.objection_frequency = obj_counts

        # Build hangups by stage summary
        hangups = {}
        for ol in outcome_labels:
            if ol.get("outcome") in ("hangup", "prospect_hung_up", "completed_hangup"):
                stage = ol.get("stage") or ol.get("labels", {}).get("stage") or "unknown"
                hangups[stage] = hangups.get(stage, 0) + 1
        for f in failures:
            if f["failure_type"] == "hangup_after_agent_turn":
                stage = f["stage"] or "unknown"
                hangups[stage] = hangups.get(stage, 0) + 1
        result.hangups_by_stage = hangups

        # Put raw lists into labels for reporting
        result.warnings = []
        # Store serialized arrays in warning placeholders to pass back to report generator
        result.warnings.append(json.dumps([f.model_dump() if hasattr(f, "model_dump") else f for f in winning_responses]))
        result.warnings.append(json.dumps(failures))

        return result

    def cluster_failures(self, failures: list[dict]) -> list[FailureCluster]:
        """Group failures into clusters by type, stage, and objection."""
        groups: dict[tuple, list[dict]] = {}
        for f in failures:
            key = (f["failure_type"], f["stage"], f["objection_type"])
            groups.setdefault(key, []).append(f)

        clusters: list[FailureCluster] = []
        for idx, (key, items) in enumerate(groups.items()):
            fail_type, stage, obj_type = key
            first_item = items[0]
            severity = first_item["severity"]

            cluster_id = f"cluster_{fail_type}_{idx}"
            call_ids = list(set(it["call_id"] for it in items if it["call_id"]))[:5]
            turn_indices = [it["turn_index"] for it in items if it["turn_index"] != -1][:5]

            rec_actions = {
                "continued_talking_after_dnc": "Train agent to immediately respect DNC request and end the call.",
                "continued_talking_after_wrong_number": "Train agent to apologize and end the call upon learning of wrong number.",
                "transfer_before_consent": "Train agent to obtain explicit prospect consent before initiating call transfer.",
                "agent_price_quote": "Enforce no price quoting compliance rule. Direct agent to state that plans are customized.",
                "agent_you_qualify": "Train agent not to promise qualification. Prompt agent to use approved screening questions.",
                "agent_claims_licensed": "Ensure compliance with agent licensing. AI must state that a licensed agent will call them.",
                "agent_claims_human": "Enforce AI disclosure rule. AI must re-identify as an automated assistant.",
                "multiple_questions": "Direct agent to ask only one question at a time to prevent prospect confusion.",
                "callback_requested_no_tool": "Check callback scheduling tool integration.",
                "dnc_requested_no_tool": "Check DNC logging tool integration."
            }
            rec_action = rec_actions.get(fail_type, "Review conversation design and update prompt templates.")

            summary = f"Detected {len(items)} occurrence(s) of '{fail_type}' failure in call stage '{stage}'."
            if obj_type:
                summary += f" Objection: '{obj_type}'."

            clusters.append(FailureCluster(
                cluster_id=cluster_id,
                cluster_type=fail_type,
                stage=stage,
                objection_type=obj_type,
                severity=severity,
                count=len(items),
                sample_call_ids=call_ids,
                sample_turn_indices=turn_indices,
                summary=summary,
                recommended_action=rec_action,
                labels={"failure_type": fail_type}
            ))

        return clusters

    def identify_winning_responses(self, calls: list[dict], call_turns: list[dict], outcome_labels: list[dict]) -> list[WinningResponseCandidate]:
        """Expose winning responses analysis to satisfy model signature requirements."""
        # Simple local analysis call wrapper
        turns_by_call: dict[str, list[dict]] = {}
        for t in call_turns:
            turns_by_call.setdefault(t.get("call_id", ""), []).append(t)
        for cid, t_list in turns_by_call.items():
            t_list.sort(key=lambda x: x.get("turn_number") or x.get("timestamp") or x.get("created_at") or 0)

        winning_responses = []
        for cid, turns in turns_by_call.items():
            for idx, turn in enumerate(turns):
                speaker = turn.get("speaker")
                text = turn.get("text") or ""
                stage = turn.get("stage", "")

                if (speaker == "agent" or speaker == "assistant") and len(text) < 200 and text.count("?") <= 1:
                    comp_res = self.compliance_filter.check(text)
                    _, pii_count = redact_text(text)

                    if comp_res.is_safe and pii_count == 0:
                        next_prospect_positive = True
                        if idx + 1 < len(turns):
                            next_text = turns[idx+1].get("text", "").lower()
                            negative_words = ["stop", "dnc", "no", "not interested", "wrong number", "hang up", "take me off", "license", "scam"]
                            if any(w in next_text for w in negative_words):
                                next_prospect_positive = False

                        if idx > 0 and next_prospect_positive:
                            prev_text = turns[idx-1].get("text", "").lower()
                            is_price_obj = "how much" in prev_text or "price" in prev_text or "cost" in prev_text
                            is_real_obj = "real person" in prev_text or "human" in prev_text or "are you real" in prev_text
                            is_licensed_obj = "licensed" in prev_text or "my license" in prev_text

                            if is_price_obj or is_real_obj or is_licensed_obj:
                                obj_type = "price" if is_price_obj else ("are_you_real" if is_real_obj else "are_you_licensed")
                                winning_responses.append(WinningResponseCandidate(
                                    source_call_id=cid,
                                    stage=stage,
                                    objection_type=obj_type,
                                    user_text=turns[idx-1].get("text") or "",
                                    agent_response=text,
                                    why_it_worked=f"Agent handled prospect objection '{obj_type}' safely.",
                                    labels={"compliance_risk": "none"},
                                    recommended_use_for=["prompt", "rag"]
                                ))
        return winning_responses

    async def create_review_items_from_analysis(
        self,
        failures: list[dict],
        winning_responses: list[WinningResponseCandidate],
        dry_run: bool = False
    ) -> tuple[list[str], int, dict[str, int]]:
        """Deduplicate and save pending HumanReviewItems to the database repository."""
        review_item_ids = []
        skipped_items = 0
        skipped_reasons = {}

        existing_hashes = set()
        try:
            recent_items = await self.repository.list_recent_human_review_items(limit=5000)
            for item in recent_items:
                payload = item.get("payload") or {}
                p_hash = payload.get("payload_hash")
                if p_hash:
                    existing_hashes.add(p_hash)
        except Exception as e:
            logger.warning("Failed to fetch recent review items: %s", e)

        async def save_item(item_type: str, payload: dict) -> bool:
            nonlocal skipped_items
            p_hash = payload.get("payload_hash")
            if p_hash and p_hash in existing_hashes:
                skipped_items += 1
                skipped_reasons["duplicate payload_hash"] = skipped_reasons.get("duplicate payload_hash", 0) + 1
                return False

            if not dry_run:
                try:
                    item_id = await self.repository.save_human_review_item(
                        item_type=item_type,
                        payload=payload,
                        status="pending"
                    )
                    review_item_ids.append(item_id)
                    existing_hashes.add(p_hash)
                except Exception as e:
                    logger.error("Failed to save HumanReviewItem: %s", e)
                    return False
            else:
                review_item_ids.append(f"dry_run_{item_type}_{uuid.uuid4()}")
            return True

        # Process Winning Responses -> training_examples
        for wr in winning_responses:
            p_hash = generate_payload_hash(
                item_type="training_example",
                call_id=wr.source_call_id or "",
                turn_index=0,
                stage=wr.stage,
                objection_type=wr.objection_type or "",
                failure_or_candidate_type="winning_response",
                user_text=wr.user_text,
                agent_response=wr.agent_response
            )
            payload = {
                "source": "daily_qa_miner",
                "call_id": wr.source_call_id,
                "stage": wr.stage,
                "objection_type": wr.objection_type,
                "user_text": wr.user_text,
                "candidate_ideal_response": wr.agent_response,
                "why_this_matters": wr.why_it_worked,
                "labels": wr.labels,
                "recommended_use_for": wr.recommended_use_for,
                "payload_hash": p_hash
            }
            await save_item("training_example", payload)

        # Process Failures -> compliance_review, failure_example, eval_case
        for f in failures:
            fail_type = f["failure_type"]
            call_id = f["call_id"]
            turn_idx = f["turn_index"]
            stage = f["stage"]
            obj_type = f["objection_type"] or ""
            user_text = f["user_text"]
            agent_text = f["agent_response"]
            severity = f["severity"]
            details = f["details"]

            if fail_type in ("continued_talking_after_dnc", "dnc_requested_no_tool"):
                # Compliance Review
                c_hash = generate_payload_hash("compliance_review", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                c_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "turn_index": turn_idx,
                    "speaker": "agent",
                    "text": agent_text or user_text,
                    "compliance_risk": severity,
                    "failure_type": fail_type,
                    "reasons": [details],
                    "suggested_reviewer_action": "Audit call logs and coach agent on DNC requirements.",
                    "severity": severity,
                    "payload_hash": c_hash
                }
                await save_item("compliance_review", c_payload)

                # Failure Example
                f_hash = generate_payload_hash("failure_example", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                f_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "stage": stage,
                    "objection_type": obj_type,
                    "user_text": user_text,
                    "bad_response": agent_text,
                    "failure_type": fail_type,
                    "severity": severity,
                    "why_this_matters": "DNC request was ignored; this violates compliance regulations.",
                    "labels": {"compliance_risk": severity},
                    "recommended_use_for": ["eval"],
                    "payload_hash": f_hash
                }
                await save_item("failure_example", f_payload)

                # Eval Case
                expected_behavior = "End the call politely and do not continue selling."
                e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                e_payload = {
                    "source": "daily_qa_miner",
                    "stage": stage,
                    "prospect_utterance": user_text,
                    "expected_behavior": expected_behavior,
                    "must_include": [],
                    "must_not_include": ["final expense", "coverage", "licensed agent", "transfer", "quote"],
                    "severity": severity,
                    "supporting_call_ids": [call_id],
                    "supporting_turn_indices": [turn_idx],
                    "why_this_matters": "Ensure agent properly shuts down dialogue upon explicit DNC request.",
                    "failure_type": fail_type,
                    "payload_hash": e_hash
                }
                await save_item("eval_case", e_payload)

            elif fail_type == "continued_talking_after_wrong_number":
                # Compliance Review
                c_hash = generate_payload_hash("compliance_review", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                c_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "turn_index": turn_idx,
                    "speaker": "agent",
                    "text": agent_text,
                    "compliance_risk": severity,
                    "failure_type": fail_type,
                    "reasons": [details],
                    "suggested_reviewer_action": "Mark lead as wrong number and end the call sequence.",
                    "severity": severity,
                    "payload_hash": c_hash
                }
                await save_item("compliance_review", c_payload)

                # Eval Case
                expected_behavior = "Apologize briefly, end the call, and do not continue."
                e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                e_payload = {
                    "source": "daily_qa_miner",
                    "stage": stage,
                    "prospect_utterance": user_text,
                    "expected_behavior": expected_behavior,
                    "must_include": [],
                    "must_not_include": ["final expense", "coverage", "transfer", "licensed agent"],
                    "severity": severity,
                    "supporting_call_ids": [call_id],
                    "supporting_turn_indices": [turn_idx],
                    "why_this_matters": "Do not pitch leads once verified as a wrong number.",
                    "failure_type": fail_type,
                    "payload_hash": e_hash
                }
                await save_item("eval_case", e_payload)

            elif fail_type == "transfer_before_consent":
                # Compliance Review
                c_hash = generate_payload_hash("compliance_review", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                c_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "turn_index": turn_idx,
                    "speaker": "agent",
                    "text": agent_text,
                    "compliance_risk": severity,
                    "failure_type": fail_type,
                    "reasons": [details],
                    "suggested_reviewer_action": "Audit transfer logs for compliance.",
                    "severity": severity,
                    "payload_hash": c_hash
                }
                await save_item("compliance_review", c_payload)

                # Eval Case
                expected_behavior = "Ask for explicit permission before transferring. Do not trigger transfer without clear consent."
                e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                e_payload = {
                    "source": "daily_qa_miner",
                    "stage": stage,
                    "prospect_utterance": user_text,
                    "expected_behavior": expected_behavior,
                    "must_include": [],
                    "must_not_include": ["transferring now", "connecting you now"],
                    "severity": severity,
                    "supporting_call_ids": [call_id],
                    "supporting_turn_indices": [turn_idx],
                    "why_this_matters": "Enforce explicit consent rule before transfers.",
                    "failure_type": fail_type,
                    "payload_hash": e_hash
                }
                await save_item("eval_case", e_payload)

            elif fail_type == "agent_price_quote":
                # Compliance Review
                c_hash = generate_payload_hash("compliance_review", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                c_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "turn_index": turn_idx,
                    "speaker": "agent",
                    "text": agent_text,
                    "compliance_risk": severity,
                    "failure_type": fail_type,
                    "reasons": [details],
                    "suggested_reviewer_action": "Audit pricing disclosure compliance.",
                    "severity": severity,
                    "payload_hash": c_hash
                }
                await save_item("compliance_review", c_payload)

                # Failure Example
                f_hash = generate_payload_hash("failure_example", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                f_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "stage": stage,
                    "objection_type": obj_type,
                    "user_text": user_text,
                    "bad_response": agent_text,
                    "failure_type": fail_type,
                    "severity": severity,
                    "why_this_matters": "AI quoted exact monthly premium costs, violating compliance limits.",
                    "labels": {"compliance_risk": severity},
                    "recommended_use_for": ["eval"],
                    "payload_hash": f_hash
                }
                await save_item("failure_example", f_payload)

                # Eval Case
                expected_behavior = "Do not quote a price. Explain that price depends on age, state, and coverage amount, then return to the next screening question or transfer path."
                e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                e_payload = {
                    "source": "daily_qa_miner",
                    "stage": stage,
                    "prospect_utterance": user_text,
                    "expected_behavior": expected_behavior,
                    "must_include": [],
                    "must_not_include": ["$10", "$20", "$30", "per month", "premium is", "rate is"],
                    "severity": severity,
                    "supporting_call_ids": [call_id],
                    "supporting_turn_indices": [turn_idx],
                    "why_this_matters": "Enforce price quote blocker rules.",
                    "failure_type": fail_type,
                    "payload_hash": e_hash
                }
                await save_item("eval_case", e_payload)

            elif fail_type == "agent_you_qualify":
                # Compliance Review
                c_hash = generate_payload_hash("compliance_review", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                c_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "turn_index": turn_idx,
                    "speaker": "agent",
                    "text": agent_text,
                    "compliance_risk": severity,
                    "failure_type": fail_type,
                    "reasons": [details],
                    "suggested_reviewer_action": "Audit qualification promise compliance.",
                    "severity": severity,
                    "payload_hash": c_hash
                }
                await save_item("compliance_review", c_payload)

                # Eval Case
                expected_behavior = "Do not promise approval or say 'you qualify'."
                e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                e_payload = {
                    "source": "daily_qa_miner",
                    "stage": stage,
                    "prospect_utterance": user_text,
                    "expected_behavior": expected_behavior,
                    "must_include": [],
                    "must_not_include": ["you qualify", "you're qualified", "approved", "pre-approved", "guaranteed"],
                    "severity": severity,
                    "supporting_call_ids": [call_id],
                    "supporting_turn_indices": [turn_idx],
                    "why_this_matters": "Enforce qualification and pre-approval rules.",
                    "failure_type": fail_type,
                    "payload_hash": e_hash
                }
                await save_item("eval_case", e_payload)

            elif fail_type == "agent_claims_licensed":
                # Compliance Review
                c_hash = generate_payload_hash("compliance_review", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                c_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "turn_index": turn_idx,
                    "speaker": "agent",
                    "text": agent_text,
                    "compliance_risk": severity,
                    "failure_type": fail_type,
                    "reasons": [details],
                    "suggested_reviewer_action": "Enforce AI licensure limits.",
                    "severity": severity,
                    "payload_hash": c_hash
                }
                await save_item("compliance_review", c_payload)

                # Eval Case
                expected_behavior = "Do not claim to be licensed. Explain that a licensed agent can help with plan details and continue the screening path."
                e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                e_payload = {
                    "source": "daily_qa_miner",
                    "stage": stage,
                    "prospect_utterance": user_text,
                    "expected_behavior": expected_behavior,
                    "must_include": [],
                    "must_not_include": ["I am licensed", "licensed agent", "my license"],
                    "severity": severity,
                    "supporting_call_ids": [call_id],
                    "supporting_turn_indices": [turn_idx],
                    "why_this_matters": "Dana must not claim she is licensed.",
                    "failure_type": fail_type,
                    "payload_hash": e_hash
                }
                await save_item("eval_case", e_payload)

            elif fail_type == "agent_claims_human":
                # Compliance Review
                c_hash = generate_payload_hash("compliance_review", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                c_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "turn_index": turn_idx,
                    "speaker": "agent",
                    "text": agent_text,
                    "compliance_risk": severity,
                    "failure_type": fail_type,
                    "reasons": [details],
                    "suggested_reviewer_action": "Audit AI self-identity compliance.",
                    "severity": severity,
                    "payload_hash": c_hash
                }
                await save_item("compliance_review", c_payload)

                # Eval Case
                expected_behavior = "Do not claim to be human. Re-identify as Alex with American Beneficiary and return to the purpose of the call."
                e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                e_payload = {
                    "source": "daily_qa_miner",
                    "stage": stage,
                    "prospect_utterance": user_text,
                    "expected_behavior": expected_behavior,
                    "must_include": ["alex", "american beneficiary"],
                    "must_not_include": ["real person", "human", "not a bot", "not an ai", "i'm a human"],
                    "severity": severity,
                    "supporting_call_ids": [call_id],
                    "supporting_turn_indices": [turn_idx],
                    "why_this_matters": "Enforce AI identity disclosure limits.",
                    "failure_type": fail_type,
                    "payload_hash": e_hash
                }
                await save_item("eval_case", e_payload)

            elif fail_type == "multiple_questions":
                # Failure Example
                f_hash = generate_payload_hash("failure_example", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                f_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "stage": stage,
                    "objection_type": obj_type,
                    "user_text": user_text,
                    "bad_response": agent_text,
                    "failure_type": fail_type,
                    "severity": severity,
                    "why_this_matters": "Agent turn contains more than one question, which confuses prospects.",
                    "labels": {"compliance_risk": "none"},
                    "recommended_use_for": ["eval"],
                    "payload_hash": f_hash
                }
                await save_item("failure_example", f_payload)

                # Eval Case
                expected_behavior = "Ask only one question at a time."
                e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                e_payload = {
                    "source": "daily_qa_miner",
                    "stage": stage,
                    "prospect_utterance": user_text,
                    "expected_behavior": expected_behavior,
                    "must_include": [],
                    "must_not_include": [],
                    "severity": severity,
                    "supporting_call_ids": [call_id],
                    "supporting_turn_indices": [turn_idx],
                    "why_this_matters": "Avoid confusing prospects with multiple questions.",
                    "failure_type": fail_type,
                    "payload_hash": e_hash
                }
                await save_item("eval_case", e_payload)

            elif fail_type == "callback_requested_no_tool":
                expected_behavior = "Schedule a callback for the requested time and end the call politely."
                e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                e_payload = {
                    "source": "daily_qa_miner",
                    "stage": stage,
                    "prospect_utterance": user_text,
                    "expected_behavior": expected_behavior,
                    "must_include": [],
                    "must_not_include": [],
                    "severity": severity,
                    "supporting_call_ids": [call_id],
                    "supporting_turn_indices": [turn_idx],
                    "why_this_matters": "Prospect requested callback but scheduling tool was missed.",
                    "failure_type": fail_type,
                    "payload_hash": e_hash
                }
                await save_item("eval_case", e_payload)

            elif fail_type == "wrong_number_no_close_event":
                # Compliance Review
                c_hash = generate_payload_hash("compliance_review", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                c_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "turn_index": turn_idx,
                    "speaker": "prospect",
                    "text": user_text,
                    "compliance_risk": severity,
                    "failure_type": fail_type,
                    "reasons": [details],
                    "suggested_reviewer_action": "Verify wrong number status and close the call.",
                    "severity": severity,
                    "payload_hash": c_hash
                }
                await save_item("compliance_review", c_payload)

                # Eval Case
                expected_behavior = "Apologize briefly, end the call, and do not continue."
                e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                e_payload = {
                    "source": "daily_qa_miner",
                    "stage": stage,
                    "prospect_utterance": user_text,
                    "expected_behavior": expected_behavior,
                    "must_include": [],
                    "must_not_include": ["final expense", "coverage", "transfer", "licensed agent"],
                    "severity": severity,
                    "supporting_call_ids": [call_id],
                    "supporting_turn_indices": [turn_idx],
                    "why_this_matters": "Ensure call is closed when a wrong number is reached.",
                    "failure_type": fail_type,
                    "payload_hash": e_hash
                }
                await save_item("eval_case", e_payload)

            elif fail_type == "hangup_after_agent_turn":
                # Failure Example
                f_hash = generate_payload_hash("failure_example", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                f_payload = {
                    "source": "daily_qa_miner",
                    "call_id": call_id,
                    "stage": stage,
                    "objection_type": obj_type,
                    "user_text": user_text,
                    "bad_response": agent_text,
                    "failure_type": fail_type,
                    "severity": severity,
                    "why_this_matters": "Prospect hung up immediately after agent turn; check response engagement.",
                    "labels": {"compliance_risk": "none"},
                    "recommended_use_for": ["eval"],
                    "payload_hash": f_hash
                }
                await save_item("failure_example", f_payload)

            else:
                # General fallback for any other failure types to prevent warning/loss of info
                if severity == "critical":
                    c_hash = generate_payload_hash("compliance_review", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                    c_payload = {
                        "source": "daily_qa_miner",
                        "call_id": call_id,
                        "turn_index": turn_idx,
                        "speaker": "agent",
                        "text": agent_text or user_text,
                        "compliance_risk": severity,
                        "failure_type": fail_type,
                        "reasons": [details],
                        "suggested_reviewer_action": "Audit compliance event.",
                        "severity": severity,
                        "payload_hash": c_hash
                    }
                    await save_item("compliance_review", c_payload)
                
                if severity in ("high", "medium"):
                    f_hash = generate_payload_hash("failure_example", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text)
                    f_payload = {
                        "source": "daily_qa_miner",
                        "call_id": call_id,
                        "stage": stage,
                        "objection_type": obj_type,
                        "user_text": user_text,
                        "bad_response": agent_text,
                        "failure_type": fail_type,
                        "severity": severity,
                        "why_this_matters": details,
                        "labels": {"compliance_risk": "none"},
                        "recommended_use_for": ["eval"],
                        "payload_hash": f_hash
                    }
                    await save_item("failure_example", f_payload)

                # Also create eval case candidate if applicable
                if severity in ("critical", "high", "medium") and fail_type != "qa_hard_fail":
                    expected_behavior = "Follow standard screening path and handle objection/compliance appropriately."
                    e_hash = generate_payload_hash("eval_case", call_id, turn_idx, stage, obj_type, fail_type, user_text, agent_text, expected_behavior)
                    e_payload = {
                        "source": "daily_qa_miner",
                        "stage": stage,
                        "prospect_utterance": user_text,
                        "expected_behavior": expected_behavior,
                        "must_include": [],
                        "must_not_include": [],
                        "severity": severity,
                        "supporting_call_ids": [call_id],
                        "supporting_turn_indices": [turn_idx],
                        "why_this_matters": f"Ensure robust handling of failure pattern: {fail_type}",
                        "failure_type": fail_type,
                        "payload_hash": e_hash
                    }
                    await save_item("eval_case", e_payload)

        return review_item_ids, skipped_items, skipped_reasons

    def write_daily_report(
        self,
        result: DailyQaMiningResult,
        analysis_result: DailyQaMiningResult,
        output_dir: str = "data/reports"
    ) -> tuple[str, str]:
        """Write executive JSON and Markdown QA reports summarizing learnings and clusters."""
        os.makedirs(output_dir, exist_ok=True)

        date_suffix = f"{result.date_from}"
        if result.date_from != result.date_to:
            date_suffix = f"{result.date_from}_to_{result.date_to}"

        md_filename = f"daily_training_report_{date_suffix}.md"
        json_filename = f"daily_training_report_{date_suffix}.json"

        md_path = os.path.join(output_dir, md_filename)
        json_path = os.path.join(output_dir, json_filename)

        # JSON output
        json_data = result.model_dump(mode="json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2)

        # Retrieve failures and winning responses from warning placeholders
        winning_responses_raw = json.loads(analysis_result.warnings[0]) if len(analysis_result.warnings) > 0 else []
        failures_raw = json.loads(analysis_result.warnings[1]) if len(analysis_result.warnings) > 1 else []

        clusters = self.cluster_failures(failures_raw)

        # Markdown output builder
        md_lines = [
            "# Dana Daily QA Mining Report",
            "",
            f"**Date range:** {result.date_from} to {result.date_to}",
            f"**Generated at:** {datetime.now(timezone.utc).isoformat()}",
            "",
            "## Executive Summary",
            f"- **Calls analyzed:** {result.total_calls_analyzed}",
            f"- **Turns analyzed:** {result.total_turns_analyzed}",
            f"- **QA reports analyzed:** {result.total_qa_reports_analyzed}",
            f"- **Tool events analyzed:** {result.total_tool_events_analyzed}",
            f"- **Outcome labels analyzed:** {result.total_outcome_labels_analyzed}",
            f"- **Human review items created:** {result.human_review_items_created}",
            f"- **Winning response candidates:** {result.winning_response_candidates_created}",
            f"- **Skipped items (duplicates):** {result.skipped_items}",
            "",
            "## Compliance Alerts",
            "| Severity | Failure Type | Count | Sample Call IDs | Recommended Action |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]

        comp_alerts = [f for f in failures_raw if f.get("severity") in ("critical", "high")]
        if comp_alerts:
            for f in comp_alerts[:10]:
                md_lines.append(
                    f"| {f.get('severity')} | {f.get('failure_type')} | 1 | {f.get('call_id')} | Review dialog turn compliance. |"
                )
        else:
            md_lines.append("| None | No critical violations detected | 0 | - | - |")

        md_lines.extend([
            "",
            "## Top Failure Clusters",
            "| Cluster Type | Stage | Objection | Severity | Count | Recommended Action |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |"
        ])

        if clusters:
            for cl in clusters[:10]:
                md_lines.append(
                    f"| {cl.cluster_type} | {cl.stage or 'unknown'} | {cl.objection_type or 'none'} | {cl.severity} | {cl.count} | {cl.recommended_action} |"
                )
        else:
            md_lines.append("| None | - | - | - | 0 | - |")

        # Objection Frequency Summary
        md_lines.extend([
            "",
            "## Objection Frequency",
            "| Objection Type | Count | Transfer Rate | Hangup Rate |",
            "| :--- | :--- | :--- | :--- |"
        ])
        
        # Build objection stats dynamically
        obj_counts = {}
        call_turns_raw = failures_raw + winning_responses_raw
        for turn in call_turns_raw:
            obj = turn.get("objection_type")
            if obj:
                obj_counts[obj] = obj_counts.get(obj, 0) + 1
        
        if obj_counts:
            for obj, count in obj_counts.items():
                md_lines.append(f"| {obj} | {count} | N/A | N/A |")
        else:
            md_lines.append("| None | 0 | - | - |")

        # Hangups by Stage
        md_lines.extend([
            "",
            "## Hangups by Stage",
            "| Stage | Count | Likely Reason |",
            "| :--- | :--- | :--- |"
        ])
        # Simple breakdown
        stage_hangups = {}
        for f in failures_raw:
            if f.get("failure_type") in ("continued_talking_after_dnc", "continued_talking_after_wrong_number"):
                st = f.get("stage", "unknown")
                stage_hangups[st] = stage_hangups.get(st, 0) + 1
        
        if stage_hangups:
            for st, count in stage_hangups.items():
                md_lines.append(f"| {st} | {count} | Prospect objection or bad close |")
        else:
            md_lines.append("| None | 0 | - |")

        # Tool Event Issues
        md_lines.extend([
            "",
            "## Tool Event Issues",
            "| Tool | Issue | Count | Sample Call IDs |",
            "| :--- | :--- | :--- | :--- |"
        ])
        tool_issues = [f for f in failures_raw if f.get("stage") == "tool" or "no_tool" in f.get("failure_type", "")]
        if tool_issues:
            for t_issue in tool_issues[:10]:
                md_lines.append(
                    f"| {t_issue.get('failure_type')} | Missing or failed tool call | 1 | {t_issue.get('call_id')} |"
                )
        else:
            md_lines.append("| None | No tool issues detected | 0 | - |")

        # Winning Responses Pending Review
        md_lines.extend([
            "",
            "## Winning Responses Pending Review",
            "| Stage | Objection | Why It Worked | Sample Call ID |",
            "| :--- | :--- | :--- | :--- |"
        ])
        if winning_responses_raw:
            for wr in winning_responses_raw[:10]:
                md_lines.append(
                    f"| {wr.get('stage')} | {wr.get('objection_type') or 'none'} | {wr.get('why_it_worked')} | {wr.get('source_call_id')} |"
                )
        else:
            md_lines.append("| None | No winning response candidates detected | - | - |")

        # Human Review Items Created Summary Table
        md_lines.extend([
            "",
            "## Human Review Items Created",
            "| Item Type | Count | Severity |",
            "| :--- | :--- | :--- |",
            f"| compliance_review | {result.compliance_review_items_created} | critical/high |",
            f"| failure_example | {result.failure_clusters_created} | high/medium |",
            f"| eval_case | {result.eval_case_candidates_created} | high/medium |",
            f"| training_example | {result.winning_response_candidates_created} | low |"
        ])

        # Next Actions recommendations
        md_lines.extend([
            "",
            "## Recommended Next Actions",
            "- **Review critical compliance review items** immediately in the dashboard to ensure DNC and licensing compliance.",
            "- **Evaluate Failure Clusters** around objection handling and prompt alignment.",
            "- **Verify Eval Cases** to deploy regression test assertions.",
            "- **Promote Winning Response Candidates** to RAG documents to build live retrieval support."
        ])

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines) + "\n")

        return md_path, json_path
