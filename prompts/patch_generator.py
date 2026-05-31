"""Safe Prompt Patch Candidate Generator.

Analyzes feedback, failures, and training reviews to propose prompt modifications.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from storage.repository import Repository
from prompts.versioning import PromptVersionManager


@dataclass
class PromptPatchCandidate:
    """A proposed modification to a prompt section."""
    patch_id: str
    prompt_name: str
    patch_type: str
    title: str
    problem_summary: str
    proposed_change_summary: str
    proposed_text: str
    rationale: str
    source_evidence: list[dict[str, Any]]
    expected_benefit: str
    risk_level: str
    compliance_impact: str
    recommended_tests: list[str]
    labels: dict[str, Any]
    payload_hash: str
    created_at: datetime
    target_section: Optional[str] = None
    insertion_point: Optional[str] = None
    original_text: Optional[str] = None


@dataclass
class PromptPatchGenerationResult:
    """Result summary of candidate patch generation."""
    prompt_name: str
    source_prompt_path: str
    source_prompt_hash: str
    total_sources_scanned: int
    candidates_generated: int
    candidates_saved: int
    candidates_skipped: int
    skipped_reasons: dict[str, int]
    review_item_ids: list[str]
    report_json_path: Optional[str] = None
    report_markdown_path: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class PromptPatchValidationResult:
    """Results of checks run against a proposed patch candidate's wording."""
    passed: bool
    critical_failures: list[str] = field(default_factory=list)
    high_failures: list[str] = field(default_factory=list)
    medium_warnings: list[str] = field(default_factory=list)
    low_warnings: list[str] = field(default_factory=list)
    patch_word_count: int = 0
    patch_line_count: int = 0
    forbidden_phrases_found: list[str] = field(default_factory=list)
    required_tests: list[str] = field(default_factory=list)


class PromptPatchGenerator:
    """Generates prompt patch candidates from failures and reviewed training data."""

    def __init__(
        self,
        repository: Optional[Repository] = None,
        prompt_version_manager: Optional[PromptVersionManager] = None,
    ) -> None:
        self.repository = repository or Repository()
        self.version_manager = prompt_version_manager or PromptVersionManager(repository=self.repository)

    async def gather_sources(self, limit: int = 500) -> dict[str, list[dict[str, Any]]]:
        """Gather recent approved human review items, training examples, and execution reports."""
        warnings_list = []

        review_items = []
        try:
            review_items = await self.repository.query_human_review_items({})
        except Exception as e:
            warnings_list.append(f"Could not load HumanReviewItems: {e}")

        training_examples = []
        try:
            training_examples = await self.repository.query_training_examples({})
        except Exception as e:
            warnings_list.append(f"Could not load TrainingExamples: {e}")

        eval_cases = []
        try:
            eval_cases = await self.repository.query_eval_cases({})
        except Exception as e:
            warnings_list.append(f"Could not load EvalCases: {e}")

        reports = []
        directories = ["data/evals", "data/simulations", "data/reports", "data/prompt_versions"]
        for d in directories:
            d_path = Path(d)
            if d_path.exists() and d_path.is_dir():
                for file in d_path.glob("*.json"):
                    try:
                        content = file.read_text(encoding="utf-8")
                        report_data = json.loads(content)
                        reports.append({
                            "source_file": str(file),
                            "report_data": report_data
                        })
                    except Exception as e:
                        warnings_list.append(f"Could not parse report file {file}: {e}")

        return {
            "human_review_items": review_items[:limit],
            "training_examples": training_examples[:limit],
            "eval_cases": eval_cases[:limit],
            "reports": reports,
            "warnings": warnings_list
        }

    def compute_payload_hash(self, candidate: PromptPatchCandidate) -> str:
        """Compute stable SHA-256 hash of a candidate's payload attributes."""
        evidence_summary = []
        for ev in candidate.source_evidence:
            summary_ev = {
                "source": ev.get("source"),
                "id": ev.get("id"),
                "file": ev.get("file"),
                "snippet": ev.get("snippet")
            }
            evidence_summary.append(summary_ev)
        # Sort evidence to ensure stability
        evidence_summary.sort(key=lambda x: (x.get("source") or "", x.get("id") or "", x.get("file") or ""))

        raw_str = (
            f"{candidate.prompt_name}:{candidate.patch_type}:{candidate.title}:"
            f"{candidate.target_section or ''}:{candidate.proposed_text}:"
            f"{json.dumps(evidence_summary)}"
        )
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    def generate_candidates_from_sources(
        self,
        prompt_name: str,
        prompt_text: str,
        source_bundle: dict[str, list[dict[str, Any]]],
    ) -> list[PromptPatchCandidate]:
        """Scan gathered source data and build deterministic prompt patch candidates."""
        evidence_by_type = {
            "transfer_consent_rule": [],
            "dnc_handling_rule": [],
            "wrong_number_handling_rule": [],
            "price_question_rule": [],
            "licensed_question_rule": [],
            "identity_question_rule": [],
            "one_question_rule": [],
            "disqualification_rule": [],
            "callback_rule": []
        }

        # 1. Scan HumanReviewItems
        for item in source_bundle.get("human_review_items") or []:
            if item.get("status") == "approved":
                payload = item.get("payload") or {}
                # Match failure types
                failure_type = payload.get("failure_type") or payload.get("labels", {}).get("failure_type")
                if not failure_type:
                    # fallback checks for strings
                    for k, v in payload.items():
                        if isinstance(v, str):
                            if "transfer_before_consent" in v or "transfer-before-consent" in v:
                                failure_type = "transfer_before_consent"
                            elif "continued_talking_after_dnc" in v or "talking_after_dnc" in v:
                                failure_type = "continued_talking_after_dnc"
                            elif "continued_talking_after_wrong_number" in v:
                                failure_type = "continued_talking_after_wrong_number"

                if failure_type == "transfer_before_consent":
                    evidence_by_type["transfer_consent_rule"].append({"source": "human_review_item", "id": item["id"], "payload": payload})
                elif failure_type == "continued_talking_after_dnc":
                    evidence_by_type["dnc_handling_rule"].append({"source": "human_review_item", "id": item["id"], "payload": payload})
                elif failure_type == "continued_talking_after_wrong_number":
                    evidence_by_type["wrong_number_handling_rule"].append({"source": "human_review_item", "id": item["id"], "payload": payload})
                elif failure_type in ("agent_price_quote", "price_question"):
                    evidence_by_type["price_question_rule"].append({"source": "human_review_item", "id": item["id"], "payload": payload})
                elif failure_type == "agent_claims_licensed":
                    evidence_by_type["licensed_question_rule"].append({"source": "human_review_item", "id": item["id"], "payload": payload})
                elif failure_type in ("agent_claims_human", "asks_if_real"):
                    evidence_by_type["identity_question_rule"].append({"source": "human_review_item", "id": item["id"], "payload": payload})
                elif failure_type == "multiple_questions":
                    evidence_by_type["one_question_rule"].append({"source": "human_review_item", "id": item["id"], "payload": payload})
                elif failure_type in ("nursing_home_mishandled", "not_decision_maker"):
                    evidence_by_type["disqualification_rule"].append({"source": "human_review_item", "id": item["id"], "payload": payload})
                elif failure_type == "callback_requested_no_tool":
                    evidence_by_type["callback_rule"].append({"source": "human_review_item", "id": item["id"], "payload": payload})

        # 2. Scan local reports
        for report in source_bundle.get("reports") or []:
            file_name = report["source_file"]
            data = report["report_data"]
            report_str = json.dumps(data).lower()

            if "transfer_before_consent" in report_str or "transfer-before-consent" in report_str:
                evidence_by_type["transfer_consent_rule"].append({"source": "report", "file": file_name, "snippet": "Found transfer before consent failure in report"})
            if "continued_talking_after_dnc" in report_str or "talking_after_dnc" in report_str or "dnc_failure" in report_str:
                evidence_by_type["dnc_handling_rule"].append({"source": "report", "file": file_name, "snippet": "Found DNC handling failure in report"})
            if "continued_talking_after_wrong_number" in report_str or "wrong_number_failure" in report_str:
                evidence_by_type["wrong_number_handling_rule"].append({"source": "report", "file": file_name, "snippet": "Found wrong number failure in report"})
            if "agent_price_quote" in report_str or "price_question" in report_str or "price_failure" in report_str:
                evidence_by_type["price_question_rule"].append({"source": "report", "file": file_name, "snippet": "Found price quoting failure in report"})
            if "agent_claims_licensed" in report_str or "licensed_failure" in report_str:
                evidence_by_type["licensed_question_rule"].append({"source": "report", "file": file_name, "snippet": "Found licensing failure in report"})
            if "agent_claims_human" in report_str or "asks_if_real" in report_str or "human_failure" in report_str:
                evidence_by_type["identity_question_rule"].append({"source": "report", "file": file_name, "snippet": "Found identity claim failure in report"})
            if "multiple_questions" in report_str or "one_question_failure" in report_str:
                evidence_by_type["one_question_rule"].append({"source": "report", "file": file_name, "snippet": "Found stacked question failure in report"})
            if "nursing_home_mishandled" in report_str or "not_decision_maker" in report_str or "disqualification_failure" in report_str:
                evidence_by_type["disqualification_rule"].append({"source": "report", "file": file_name, "snippet": "Found disqualification failure in report"})
            if "callback_requested_no_tool" in report_str or "callback_failure" in report_str:
                evidence_by_type["callback_rule"].append({"source": "report", "file": file_name, "snippet": "Found callback handling failure in report"})

        candidates = []

        # Define candidate details mapping
        template_configs = {
            "transfer_consent_rule": {
                "title": "Strengthen explicit transfer consent rule",
                "problem": "Agent attempted transfers before obtaining clear consent from the prospect.",
                "proposed_summary": "Require explicit verbal consent before initiating a transfer.",
                "target_section": "STRICT COMPLIANCE & GUARDRAILS",
                "proposed_text": (
                    "- Dana must ask permission before transferring.\n"
                    "- Transfer requires clear consent such as “yes,” “go ahead,” “connect me,” or equivalent.\n"
                    "- Dana must not say “connecting you now,” “transferring now,” or trigger transfer before consent."
                ),
                "rationale": "Avoid unauthorized transfers and ensure consent compliance.",
                "benefit": "Zero unauthorized transfers and better compliance score.",
                "risk": "low",
                "impact": "high positive",
                "tests": ["eval_cases", "transcript_replay", "prospect_simulations"],
                "labels": {"compliance_area": "consent"}
            },
            "dnc_handling_rule": {
                "title": "Improve DNC handling and immediate call termination",
                "problem": "Agent continued selling or talking after DNC request was made.",
                "proposed_summary": "End the call immediately and politely on DNC without pitching.",
                "target_section": "STRICT COMPLIANCE & GUARDRAILS",
                "proposed_text": (
                    "- If prospect says stop calling, do not call, remove me, take me off your list, end politely.\n"
                    "- Do not continue selling.\n"
                    "- Do not mention final expense, coverage, quote, transfer, or licensed agent after DNC."
                ),
                "rationale": "Ensure TCPA DNC list compliance.",
                "benefit": "Immediate call termination on DNC request.",
                "risk": "low",
                "impact": "critical positive",
                "tests": ["eval_cases", "transcript_replay", "prospect_simulations"],
                "labels": {"compliance_area": "dnc"}
            },
            "wrong_number_handling_rule": {
                "title": "Handle wrong numbers immediately",
                "problem": "Agent continued pitch after prospect stated it was a wrong number.",
                "proposed_summary": "Apologize and end call immediately on wrong numbers.",
                "target_section": "STRICT COMPLIANCE & GUARDRAILS",
                "proposed_text": (
                    "- If prospect says wrong number/wrong person/not me, apologize briefly, mark wrong number if available, and end.\n"
                    "- Do not pitch or transfer."
                ),
                "rationale": "Prevent unwanted calling of wrong parties.",
                "benefit": "Clean call termination on wrong numbers.",
                "risk": "low",
                "impact": "high positive",
                "tests": ["eval_cases", "transcript_replay", "prospect_simulations"],
                "labels": {"compliance_area": "wrong_number"}
            },
            "price_question_rule": {
                "title": "Enforce price quoting prohibition",
                "problem": "Agent gave price quotes or monthly premium estimations.",
                "proposed_summary": "Refuse price quotes and refer to licensed agent.",
                "target_section": "STRICT COMPLIANCE & GUARDRAILS",
                "proposed_text": (
                    "- Do not quote an exact price, rate, premium, or monthly cost.\n"
                    "- Safe response: “That depends on your age, state, and how much coverage you want. A licensed agent can review the exact options with you.”\n"
                    "- Then return to one screening question or transfer-consent path."
                ),
                "rationale": "Adhere to licensed-only price quoting restrictions.",
                "benefit": "Zero price quotes by screening agent.",
                "risk": "low",
                "impact": "critical positive",
                "tests": ["eval_cases", "transcript_replay", "prospect_simulations"],
                "labels": {"compliance_area": "pricing"}
            },
            "licensed_question_rule": {
                "title": "Enforce licensing disclaimer rule",
                "problem": "Agent claimed to be licensed or failed to clarify they are unlicensed.",
                "proposed_summary": "State clearly that the agent is unlicensed.",
                "target_section": "STRICT COMPLIANCE & GUARDRAILS",
                "proposed_text": (
                    "- Dana must not claim she is licensed.\n"
                    "- Safe response: “I’m not the licensed agent. A licensed agent can review plan details with you.”\n"
                    "- Do not say “I am licensed,” “my license,” or “I’m a licensed agent.”"
                ),
                "rationale": "Ensure compliance with insurance state licensing laws.",
                "benefit": "Clear disclaimer of unlicensed status.",
                "risk": "low",
                "impact": "critical positive",
                "tests": ["eval_cases", "transcript_replay", "prospect_simulations"],
                "labels": {"compliance_area": "licensing"}
            },
            "identity_question_rule": {
                "title": "Enforce identity and automation disclosure rules",
                "problem": "Agent claimed to be a real human or hid automated status when asked.",
                "proposed_summary": "State identity without claiming human status.",
                "target_section": "STRICT COMPLIANCE & GUARDRAILS",
                "proposed_text": (
                    "- Dana must not claim to be human, a real person, not a bot, or not AI.\n"
                    "- Safe response: “This is Alex with American Beneficiary. I’m checking if you’re still open to looking at final expense options.”\n"
                    "- Return to the purpose of the call."
                ),
                "rationale": "Maintain transparency and avoid deceptive claims.",
                "benefit": "Truthful identity statements without human pretense.",
                "risk": "low",
                "impact": "high positive",
                "tests": ["eval_cases", "transcript_replay", "prospect_simulations"],
                "labels": {"compliance_area": "identity"}
            },
            "one_question_rule": {
                "title": "Enforce one-question-at-a-time guidance",
                "problem": "Agent stacked multiple questions in a single turn.",
                "proposed_summary": "Only ask one screening question per response.",
                "target_section": "HUMAN SPEAKING STYLE",
                "proposed_text": (
                    "- Ask one question per turn.\n"
                    "- Do not stack age, living situation, decision maker, and transfer questions in one response.\n"
                    "- Keep each Dana response short and spoken."
                ),
                "rationale": "Ensure conversational flow is natural and not overwhelming.",
                "benefit": "Natural conversational pacing.",
                "risk": "low",
                "impact": "medium positive",
                "tests": ["eval_cases", "transcript_replay", "prospect_simulations"],
                "labels": {"compliance_area": "style"}
            },
            "disqualification_rule": {
                "title": "Strengthen disqualification screening",
                "problem": "Agent transferred unqualified prospects (nursing home / not decision maker).",
                "proposed_summary": "Terminate call for nursing home or non-decision maker prospects.",
                "target_section": "STRICT COMPLIANCE & GUARDRAILS",
                "proposed_text": (
                    "- Do not transfer as qualified when prospect is in nursing home/assisted living or does not handle decisions.\n"
                    "- Confirm briefly if needed, then end or mark disqualified."
                ),
                "rationale": "Optimize transfer quality and agent conversion rates.",
                "benefit": "Only qualified transfers are connected.",
                "risk": "low",
                "impact": "high positive",
                "tests": ["eval_cases", "transcript_replay", "prospect_simulations"],
                "labels": {"compliance_area": "qualification"}
            },
            "callback_rule": {
                "title": "Strengthen callback request handling",
                "problem": "Agent continued pitch after callback request was made.",
                "proposed_summary": "Acknowledge callback request and stop pitching.",
                "target_section": "STRICT COMPLIANCE & GUARDRAILS",
                "proposed_text": (
                    "- If prospect asks for a callback or says they are busy/driving/at work, acknowledge and set/record callback when available.\n"
                    "- Do not continue pitching after callback request."
                ),
                "rationale": "Respect prospect schedule and avoid aggressive selling.",
                "benefit": "Polite callback scheduling without aggressive pitches.",
                "risk": "low",
                "impact": "medium positive",
                "tests": ["eval_cases", "transcript_replay", "prospect_simulations"],
                "labels": {"compliance_area": "callbacks"}
            }
        }

        # Build failure-based candidates
        for ptype, evidence in evidence_by_type.items():
            if evidence:
                cfg = template_configs[ptype]
                candidate = PromptPatchCandidate(
                    patch_id=str(uuid.uuid4()),
                    prompt_name=prompt_name,
                    patch_type=ptype,
                    title=cfg["title"],
                    problem_summary=cfg["problem"],
                    proposed_change_summary=cfg["proposed_summary"],
                    proposed_text=cfg["proposed_text"],
                    rationale=cfg["rationale"],
                    source_evidence=evidence,
                    expected_benefit=cfg["benefit"],
                    risk_level=cfg["risk"],
                    compliance_impact=cfg["impact"],
                    recommended_tests=cfg["tests"],
                    labels=cfg["labels"],
                    payload_hash="",
                    created_at=datetime.now(timezone.utc),
                    target_section=cfg["target_section"]
                )
                candidate.payload_hash = self.compute_payload_hash(candidate)
                candidates.append(candidate)

        # 3. Handle TrainingExamples winning examples
        for ex in source_bundle.get("training_examples") or []:
            if ex.get("approved_by") and "prompt" in (ex.get("use_for") or []):
                ideal = ex.get("ideal_response", "")
                user = ex.get("user_text", "")
                stage = ex.get("stage", "unknown")

                # Validate to ensure no PII or long words
                if len(ideal.split()) < 35 and "$" not in ideal:
                    candidate = PromptPatchCandidate(
                        patch_id=str(uuid.uuid4()),
                        prompt_name=prompt_name,
                        patch_type="add_example_response",
                        title=f"Add example response for stage '{stage}'",
                        problem_summary=f"Incorporate approved training responses for the '{stage}' stage.",
                        proposed_change_summary="Add concrete example phrases to guide conversational style.",
                        proposed_text=f"- Example response for stage '{stage}': \"{ideal}\" (User asked: \"{user}\")",
                        rationale="Improve stylistic accuracy using approved winning coaching examples.",
                        source_evidence=[{"source": "training_example", "id": ex["id"], "ideal_response": ideal, "user_text": user}],
                        expected_benefit="Better matching of conversational tone and flow.",
                        risk_level="low",
                        compliance_impact="low positive",
                        recommended_tests=["eval_cases"],
                        labels={"compliance_area": "examples", "stage": stage},
                        payload_hash="",
                        created_at=datetime.now(timezone.utc),
                        target_section="HUMAN SPEAKING STYLE"
                    )
                    candidate.payload_hash = self.compute_payload_hash(candidate)
                    candidates.append(candidate)

        return candidates

    def validate_patch_candidate(
        self,
        candidate: PromptPatchCandidate,
        prompt_text: str,
    ) -> PromptPatchValidationResult:
        """Validate proposed patch candidate text for compliance and style violations."""
        critical_failures = []
        high_failures = []
        medium_warnings = []
        low_warnings = []
        forbidden_phrases_found = []

        proposed_text = candidate.proposed_text
        proposed_lower = proposed_text.lower()
        negations = ["never", "do not", "don't", "prohibit", "forbidden", "not ", "no ", "can't", "cannot", "avoid", "must not"]

        def check_unnegated(pat: str) -> list[str]:
            matches = []
            for line in proposed_text.splitlines():
                line_lower = line.lower()
                if re.search(pat, line_lower):
                    if not any(neg in line_lower for neg in negations):
                        matches.append(line.strip())
            return matches

        # Critical Checks
        # Say you qualify
        qualify_matches = check_unnegated(r"\bsay\s+['\"]?you\s+qualify\b|\btell\s+.*you\s+qualify\b|\bclaim\s+.*you\s+qualify\b")
        if qualify_matches:
            critical_failures.append("Proposed text instructs agent to say 'you qualify' without negation.")
            forbidden_phrases_found.extend(qualify_matches)

        # Approved / pre-approved
        approved_matches = check_unnegated(r"\bsay\s+['\"]?you\s+(?:are|'re)\s+approved\b|\btell\s+.*you\s+(?:are|'re)\s+approved\b|\bclaim\s+.*you\s+(?:are|'re)\s+approved\b|\bpromise\s+approval\b|\bguaranteed\s+approval\b")
        if approved_matches:
            critical_failures.append("Proposed text instructs agent to say prospect is approved/pre-approved.")
            forbidden_phrases_found.extend(approved_matches)

        # Price quote or monthly cost (check dollar amounts)
        price_matches = check_unnegated(r"\bexact\s+price\s+quote\b|\bquote\s+is\s+\$\s?\d+|\bexact\s+price\b|\bmonthly\s+cost\s+is\s+\$\s?\d+|\bpremium\s+is\s+\$\s?\d+|\$\s?\d+\b")
        if price_matches:
            critical_failures.append("Proposed text instructs agent to quote exact prices/premiums.")
            forbidden_phrases_found.extend(price_matches)

        # Claim licensed
        licensed_matches = check_unnegated(r"\byou\s+are\s+(?:a\s+)?licensed\s+agent\b|\bi\s+am\s+(?:a\s+)?licensed\s+agent\b|\bi'm\s+(?:a\s+)?licensed\s+agent\b|\bclaim\s+(?:to\s+be\s+)?licensed\b")
        if licensed_matches:
            critical_failures.append("Proposed text instructs agent to claim they are licensed.")
            forbidden_phrases_found.extend(licensed_matches)

        # Claim human
        human_matches = check_unnegated(r"\bpretend\s+you\s+are\s+(?:a\s+)?(?:real\s+person|human)\b|\bsay\s+you\s+are\s+(?:a\s+)?(?:real\s+person|human)\b|\bclaim\s+you\s+are\s+(?:a\s+)?(?:real\s+person|human)\b|\byou\s+are\s+a\s+real\s+person\b|\byou\s+are\s+a\s+human\b")
        if human_matches:
            critical_failures.append("Proposed text instructs agent to claim they are human or a real person.")
            forbidden_phrases_found.extend(human_matches)

        # Sensitive info (SSN, routing, medicare, bank, dob)
        sensitive_matches = check_unnegated(r"\b(?:ask\s+for|get|collect|request|obtain)\s+.*(?:social\s+security|ssn|bank|routing|credit\s+card|medicare|date\s+of\s+birth|dob)")
        if sensitive_matches:
            critical_failures.append("Proposed text instructs agent to ask for sensitive information (SSN, Bank, Credit Card, DOB).")
            forbidden_phrases_found.extend(sensitive_matches)

        # Transfer without explicit consent
        consent_matches = check_unnegated(r"transfer.*without.*(?:consent|asking)|auto-transfer|transfer\s+immediately")
        if consent_matches:
            critical_failures.append("Proposed text instructs agent to transfer without explicit consent.")
            forbidden_phrases_found.extend(consent_matches)

        # Ignore DNC
        dnc_matches = check_unnegated(r"ignore.*dnc|ignore.*do\s+not\s+call|bypass.*dnc")
        if dnc_matches:
            critical_failures.append("Proposed text instructs agent to ignore do not call requests.")
            forbidden_phrases_found.extend(dnc_matches)

        # Continue after wrong number
        wrong_num_matches = check_unnegated(r"continue.*wrong\s+number|ignore.*wrong\s+number")
        if wrong_num_matches:
            critical_failures.append("Proposed text instructs agent to continue after a wrong number is identified.")
            forbidden_phrases_found.extend(wrong_num_matches)

        # Bypass/override compliance filters
        compliance_matches = check_unnegated(r"bypass.*compliance|override.*safety|override.*compliance|ignore.*safety")
        if compliance_matches:
            critical_failures.append("Proposed text instructs agent to bypass compliance filters or override safety rules.")
            forbidden_phrases_found.extend(compliance_matches)

        # High Checks
        mult_q_matches = check_unnegated(r"\b(?:ask|send|give)\s+multiple\s+questions\b|\bstack\s+questions\b|\blong\s+responses\b")
        if mult_q_matches:
            high_failures.append("Proposed text encourages long multi-question responses or stacked questions.")
            forbidden_phrases_found.extend(mult_q_matches)

        pressure_matches = check_unnegated(r"\bpressure\b.*\bnot\s+interested\b|\bpush\b.*\bnot\s+interested\b|\bkeep\s+trying\b.*\bnot\s+interested\b")
        if pressure_matches:
            high_failures.append("Proposed text encourages pressuring prospects who are not interested.")
            forbidden_phrases_found.extend(pressure_matches)

        minute_matches = check_unnegated(r"\bonly\s+takes?\s+a\s+minute\b|\btakes?\s+a\s+minute\b")
        if minute_matches:
            high_failures.append("Proposed text claims call only takes a minute.")
            forbidden_phrases_found.extend(minute_matches)

        gov_matches = check_unnegated(r"\bgovernment\s+benefit\b|\bstate\s+benefit\b|\bgovernment\s+program\b")
        if gov_matches:
            high_failures.append("Proposed text references government or state benefits in a potentially misleading way.")
            forbidden_phrases_found.extend(gov_matches)

        hide_matches = check_unnegated(r"\bhide\s+(?:ai|automation|automated)\b|\bdeceive\b")
        if hide_matches:
            high_failures.append("Proposed text instructs agent to hide automated nature deceptively.")
            forbidden_phrases_found.extend(hide_matches)

        guarantee_matches = check_unnegated(r"\bguaranteed\s+acceptance\b|\bguaranteed\s+coverage\b")
        if guarantee_matches:
            high_failures.append("Proposed text implies guaranteed acceptance without context.")
            forbidden_phrases_found.extend(guarantee_matches)

        # Medium checks
        if len(proposed_text.split()) > 250:
            medium_warnings.append("Proposed text is longer than 250 words.")

        if len(re.findall(r"(?:example|say:)\s*['\"-]", proposed_lower)) > 3:
            medium_warnings.append("Proposed text includes too many examples.")

        if not candidate.target_section:
            medium_warnings.append("Proposed patch has no clear target section.")

        if prompt_text and proposed_text.strip().lower() in prompt_text.lower():
            medium_warnings.append("Proposed patch duplicates an existing prompt rule already present nearly verbatim.")

        passed = len(critical_failures) == 0 and len(high_failures) == 0

        return PromptPatchValidationResult(
            passed=passed,
            critical_failures=critical_failures,
            high_failures=high_failures,
            medium_warnings=medium_warnings,
            low_warnings=low_warnings,
            patch_word_count=len(proposed_text.split()),
            patch_line_count=len(proposed_text.splitlines()),
            forbidden_phrases_found=forbidden_phrases_found,
            required_tests=list(candidate.recommended_tests)
        )

    async def save_patch_candidate(
        self,
        candidate: PromptPatchCandidate,
        source_prompt_path: str = "",
        source_prompt_hash: str = "",
        prompt_text: str = "",
    ) -> Optional[str]:
        """Save candidate to the repository as a pending HumanReviewItem if not duplicated or rejected."""
        # Run validation first
        validation_res = self.validate_patch_candidate(candidate, prompt_text)
        if not validation_res.passed:
            raise ValueError(f"Candidate failed safety validation: {validation_res.critical_failures + validation_res.high_failures}")

        # 1. Scan for existing duplicates
        existing_items = []
        try:
            existing_items = await self.repository.query_human_review_items({})
        except Exception:
            pass

        for item in existing_items:
            payload = item.get("payload") or {}
            status = item.get("status")
            item_type = item.get("item_type")
            if item_type == "prompt_patch" and payload.get("payload_hash") == candidate.payload_hash:
                if status == "rejected":
                    # Mark skip reason
                    return None
                elif status in ("pending", "approved"):
                    # Mark skip reason
                    return None

        # Build review item fields
        created_at_str = candidate.created_at.isoformat()

        payload = {
            "source": "prompt_patch_generator",
            "prompt_name": candidate.prompt_name,
            "source_prompt_path": source_prompt_path,
            "source_prompt_hash": source_prompt_hash,
            "patch_type": candidate.patch_type,
            "title": candidate.title,
            "problem_summary": candidate.problem_summary,
            "proposed_change_summary": candidate.proposed_change_summary,
            "target_section": candidate.target_section or "",
            "insertion_point": candidate.insertion_point or "",
            "original_text": candidate.original_text or "",
            "proposed_text": candidate.proposed_text,
            "rationale": candidate.rationale,
            "source_evidence": candidate.source_evidence,
            "expected_benefit": candidate.expected_benefit,
            "risk_level": candidate.risk_level,
            "compliance_impact": candidate.compliance_impact,
            "recommended_tests": candidate.recommended_tests,
            "labels": candidate.labels,
            "payload_hash": candidate.payload_hash,
            "validation": {
                "passed": validation_res.passed,
                "critical_failures": validation_res.critical_failures,
                "high_failures": validation_res.high_failures,
                "medium_warnings": validation_res.medium_warnings,
            },
            "created_at": created_at_str
        }

        # Save to DB
        saved_id = await self.repository.save_human_review_item(
            item_type="prompt_patch",
            payload=payload,
            status="pending"
        )
        return saved_id

    def write_patch_report(
        self,
        result: PromptPatchGenerationResult,
        candidates: list[PromptPatchCandidate],
        output_dir: str | Path,
    ) -> tuple[str, str]:
        """Write Markdown and JSON report files representing generated/skipped candidates."""
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        json_data = {
            "prompt_name": result.prompt_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_prompt_path": result.source_prompt_path,
            "source_prompt_hash": result.source_prompt_hash,
            "total_sources_scanned": result.total_sources_scanned,
            "candidates_generated": result.candidates_generated,
            "candidates_saved": result.candidates_saved,
            "candidates_skipped": result.candidates_skipped,
            "skipped_reasons": result.skipped_reasons,
            "review_item_ids": result.review_item_ids,
            "candidates": [
                {
                    "patch_type": c.patch_type,
                    "title": c.title,
                    "risk_level": c.risk_level,
                    "compliance_impact": c.compliance_impact,
                    "proposed_text": c.proposed_text,
                    "payload_hash": c.payload_hash
                }
                for c in candidates
            ]
        }

        json_path = out_dir / f"prompt_patch_report_{result.prompt_name}.json"
        json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")

        # Markdown compile
        md_lines = [
            "# Dana Prompt Patch Candidate Report",
            "",
            f"**Prompt:** {result.prompt_name}",
            f"**Generated at:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"**Source prompt hash:** `{result.source_prompt_hash[:12]}`",
            f"**Candidates generated:** {result.candidates_generated}",
            f"**Candidates saved:** {result.candidates_saved}",
            f"**Candidates skipped:** {result.candidates_skipped}",
            "",
            "## Executive Summary",
            "- Proposes safe corrective wording additions to patch compliance errors.",
            f"- Generated {result.candidates_generated} possible candidates from scanned files.",
            f"- Saved {result.candidates_saved} new pending review items.",
            "",
            "## Patch Candidates",
            "",
            "| patch type | title | risk level | compliance impact | recommended tests | review item id |",
            "|---|---|---|---|---|---|",
        ]

        # Candidates details table
        for i, c in enumerate(candidates):
            saved_id = result.review_item_ids[i] if i < len(result.review_item_ids) else "N/A"
            md_lines.append(
                f"| {c.patch_type} | {c.title} | {c.risk_level} | {c.compliance_impact} | `{', '.join(c.recommended_tests)}` | {saved_id} |"
            )

        md_lines.append("")
        md_lines.append("## Candidate Details")
        for c in candidates:
            md_lines.extend([
                f"\n### {c.title} (`{c.patch_type}`)",
                f"- **Problem:** {c.problem_summary}",
                f"- **Proposed Change:** {c.proposed_change_summary}",
                "- **Proposed Text:**",
                "```markdown",
                c.proposed_text,
                "```",
                f"- **Rationale:** {c.rationale}",
                f"- **Risk Level:** {c.risk_level} | **Compliance Impact:** {c.compliance_impact}",
                f"- **Recommended Tests:** `{', '.join(c.recommended_tests)}`"
            ])

        md_lines.append("")
        md_lines.append("## Skipped Candidates")
        if result.skipped_reasons:
            for reason, count in result.skipped_reasons.items():
                md_lines.append(f"- **{reason}:** {count} skipped")
        else:
            md_lines.append("- No candidates skipped.")

        md_lines.append("")
        md_lines.extend([
            "## Required Next Steps",
            "- Human review is required to approve or reject these patch candidates.",
            "- Approve/reject through the review service script.",
            "- If approved, run prompt patch application in the later Prompt 14 rollout system.",
            "- Run eval cases, replay tests, and simulations before deploying to live runtime.",
            "- Do not manually edit live production prompt files directly."
        ])

        md_path = out_dir / f"prompt_patch_report_{result.prompt_name}.md"
        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        return str(json_path), str(md_path)

    async def generate_for_prompt(
        self,
        prompt_name: str,
        prompt_path: str | Path,
        limit: int = 500,
        save_review_items: bool = True,
        output_dir: str | Path = "data/prompt_patches",
    ) -> PromptPatchGenerationResult:
        """Run candidate generation for a given prompt, checking sources, validating, and writing reports."""
        prompt_file = Path(prompt_path)
        prompt_text = ""
        prompt_hash = ""
        if prompt_file.exists():
            prompt_text = prompt_file.read_text(encoding="utf-8")
            prompt_hash = self.version_manager.compute_content_hash(prompt_text)

        source_bundle = await self.gather_sources(limit=limit)
        warnings = source_bundle.get("warnings") or []

        # Generate candidates from sources
        candidates = self.generate_candidates_from_sources(prompt_name, prompt_text, source_bundle)

        review_item_ids = []
        candidates_saved = 0
        candidates_skipped = 0
        skipped_reasons = {}

        # 1. Deduplication scans
        existing_items = []
        try:
            existing_items = await self.repository.query_human_review_items({})
        except Exception:
            pass

        final_candidates = []

        for candidate in candidates:
            # Check validation
            validation = self.validate_patch_candidate(candidate, prompt_text)
            if not validation.passed:
                skipped_reasons["failed validation"] = skipped_reasons.get("failed validation", 0) + 1
                candidates_skipped += 1
                warnings.append(f"validation_failed: candidate '{candidate.title}' failed safety validation.")
                continue

            # Check deduplication
            is_dup = False
            for item in existing_items:
                payload = item.get("payload") or {}
                status = item.get("status")
                item_type = item.get("item_type")
                if item_type == "prompt_patch" and payload.get("payload_hash") == candidate.payload_hash:
                    is_dup = True
                    reason = "previously rejected" if status == "rejected" else "duplicate prompt_patch"
                    skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
                    break

            if is_dup:
                candidates_skipped += 1
                continue

            final_candidates.append(candidate)

            if save_review_items:
                # Save review item
                saved_id = await self.save_patch_candidate(
                    candidate,
                    source_prompt_path=str(prompt_path),
                    source_prompt_hash=prompt_hash,
                    prompt_text=prompt_text
                )
                if saved_id:
                    review_item_ids.append(saved_id)
                    candidates_saved += 1
                else:
                    skipped_reasons["failed save"] = skipped_reasons.get("failed save", 0) + 1
                    candidates_skipped += 1
            else:
                review_item_ids.append(f"dry_run_not_saved_{candidate.patch_type}")

        if not save_review_items and len(final_candidates) > 0:
            warnings.append("dry_run: candidates were generated but not saved")

        total_scanned = (
            len(source_bundle.get("human_review_items") or [])
            + len(source_bundle.get("training_examples") or [])
            + len(source_bundle.get("eval_cases") or [])
            + len(source_bundle.get("reports") or [])
        )

        res_obj = PromptPatchGenerationResult(
            prompt_name=prompt_name,
            source_prompt_path=str(prompt_path),
            source_prompt_hash=prompt_hash,
            total_sources_scanned=total_scanned,
            candidates_generated=len(candidates),
            candidates_saved=candidates_saved,
            candidates_skipped=candidates_skipped,
            skipped_reasons=skipped_reasons,
            review_item_ids=review_item_ids,
            warnings=warnings
        )

        # Write reports
        json_path, md_path = self.write_patch_report(res_obj, final_candidates, output_dir)
        res_obj.report_json_path = json_path
        res_obj.report_markdown_path = md_path

        return res_obj
