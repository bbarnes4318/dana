"""Training Example Miner for Dana's continuous training system.

Mines labeled training sources for positive examples, failure candidates,
compliance review items, and eval cases, creating pending HumanReviewItem records.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field

from storage.repository import Repository


class MiningCandidate(BaseModel):
    """A mined training, compliance, or eval candidate before being saved."""

    source_id: str
    call_id: Optional[str] = None
    item_type: str
    stage: str
    user_text: str
    candidate_ideal_response: Optional[str] = None
    bad_response: Optional[str] = None
    agent_response: Optional[str] = None
    prospect_response: Optional[str] = None
    why_this_matters: str
    labels: dict[str, Any] = Field(default_factory=dict)
    recommended_use_for: list[str] = Field(default_factory=list)
    severity: str
    confidence: float
    turn_index: Optional[int] = None
    supporting_turn_indices: list[int] = Field(default_factory=list)


class MiningResult(BaseModel):
    """Summary of a mining run on a single TrainingSource."""

    source_id: str
    total_turns: int
    candidates_created: int
    skipped_candidates: int
    compliance_review_items: int
    eval_case_candidates: int
    training_example_candidates: int
    failure_candidates: int
    review_item_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def generate_payload_hash(item_type: str, payload_data: dict) -> str:
    """Generate a stable sha256 hash for deduplicating mined items."""
    canonical = {
        "source_id": payload_data.get("source_id"),
        "item_type": item_type,
        "turn_index": payload_data.get("turn_index"),
        "stage": payload_data.get("stage"),
        "user_text": payload_data.get("user_text"),
        "candidate_ideal_response": payload_data.get("candidate_ideal_response"),
        "bad_response": payload_data.get("bad_response"),
        "text": payload_data.get("text"),
    }
    serialized = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class TrainingExampleMiner:
    """Mines training/compliance/eval candidates from labeled TrainingSource objects."""

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.repository = repository

    def mine_labeled_turns(self, source_id: str, turns: list[dict]) -> list[MiningCandidate]:
        """Scans labeled turns and flags all candidates of different types."""
        candidates: list[MiningCandidate] = []

        for idx, turn in enumerate(turns):
            speaker = turn.get("speaker", "unknown")
            text = turn.get("text", "")
            turn_index = turn.get("turn_index", idx)
            label_dict = turn.get("label") or {}

            # Find closest previous prospect turn for agent context
            prev_prospect_text = ""
            prev_prospect_turn = None
            for prev_turn in reversed(turns[:idx]):
                if prev_turn.get("speaker") == "prospect":
                    prev_prospect_text = prev_turn.get("text", "")
                    prev_prospect_turn = prev_turn
                    break

            is_good_example = label_dict.get("is_good_example_candidate", False)
            is_fail_cand = label_dict.get("is_failure_candidate", False)
            compliance_risk = label_dict.get("compliance_risk", "none")

            # 1. Positive Training Examples
            if (speaker == "agent" and
                is_good_example and
                compliance_risk in ("none", "low") and
                not is_fail_cand and
                text.strip() and
                len(text.split()) <= 40 and
                text.count("?") <= 1 and
                prev_prospect_turn is not None):

                reasons = label_dict.get("reasons", [])
                why_this_matters = f"Good training example candidate for call stage '{label_dict.get('call_stage')}'. Details: {', '.join(reasons)}"

                candidates.append(MiningCandidate(
                    source_id=source_id,
                    item_type="training_example",
                    stage=label_dict.get("call_stage", "unknown"),
                    user_text=prev_prospect_text,
                    candidate_ideal_response=text,
                    bad_response=None,
                    agent_response=text,
                    prospect_response=None,
                    why_this_matters=why_this_matters,
                    labels=label_dict,
                    recommended_use_for=["prompt", "rag", "eval"],
                    severity="low",
                    confidence=label_dict.get("stage_confidence", 0.8),
                    turn_index=turn_index,
                    supporting_turn_indices=[prev_prospect_turn.get("turn_index", turn_index - 1), turn_index]
                ))

            # 2. Failure Example Candidates
            if speaker == "agent" and is_fail_cand:
                reasons = label_dict.get("reasons", [])
                why_this_matters = f"Agent response contains a failure pattern: {', '.join(reasons)}"

                candidates.append(MiningCandidate(
                    source_id=source_id,
                    item_type="failure_example",
                    stage=label_dict.get("call_stage", "unknown"),
                    user_text=prev_prospect_text,
                    candidate_ideal_response=None,
                    bad_response=text,
                    agent_response=text,
                    prospect_response=None,
                    why_this_matters=why_this_matters,
                    labels=label_dict,
                    recommended_use_for=["eval"],
                    severity="high" if compliance_risk in ("high", "critical") else "medium",
                    confidence=label_dict.get("compliance_confidence", 0.8),
                    turn_index=turn_index,
                    supporting_turn_indices=[prev_prospect_turn.get("turn_index", turn_index - 1), turn_index] if prev_prospect_turn else [turn_index]
                ))

            # 3. Compliance Review Items
            is_compliance_trigger = False
            severity = "low"
            suggested_reviewer_action = "Review for compliance."

            if speaker == "prospect":
                obj_type = label_dict.get("objection_type", "")
                call_stage = label_dict.get("call_stage", "")
                sentiment = label_dict.get("sentiment", "")
                if obj_type == "dnc" or call_stage == "dnc":
                    is_compliance_trigger = True
                    severity = "high"
                    suggested_reviewer_action = "Flag/verify DNC list removal request."
                elif obj_type == "wrong_number":
                    is_compliance_trigger = True
                    severity = "high"
                    suggested_reviewer_action = "Flag/verify wrong number request."
                elif obj_type == "hostile" or sentiment == "hostile":
                    is_compliance_trigger = True
                    severity = "high"
                    suggested_reviewer_action = "Review hostile call escalation."
            elif speaker == "agent":
                comp_risk = label_dict.get("compliance_risk", "none")
                reasons = label_dict.get("reasons", [])
                reasons_str = " ".join(reasons).lower()

                if comp_risk in ("high", "critical"):
                    is_compliance_trigger = True
                    severity = "critical" if comp_risk == "critical" else "high"
                    suggested_reviewer_action = f"Review agent compliance risk ({comp_risk})."
                elif comp_risk == "medium":
                    is_compliance_trigger = True
                    severity = "medium"
                    suggested_reviewer_action = "Review agent medium risk behavior."
                elif "dnc" in reasons_str:
                    is_compliance_trigger = True
                    severity = "critical"
                    suggested_reviewer_action = "Review agent speaking after DNC."
                elif "wrong number" in reasons_str:
                    is_compliance_trigger = True
                    severity = "critical"
                    suggested_reviewer_action = "Review agent speaking after wrong number."
                elif "transfer" in reasons_str and "consent" in reasons_str:
                    is_compliance_trigger = True
                    severity = "critical"
                    suggested_reviewer_action = "Review agent transfer without consent."

            if is_compliance_trigger:
                reasons = label_dict.get("reasons", [])
                why_this_matters = f"Compliance trigger flagged: {suggested_reviewer_action} Reasons: {', '.join(reasons)}"

                candidates.append(MiningCandidate(
                    source_id=source_id,
                    item_type="compliance_review",
                    stage=label_dict.get("call_stage", "unknown"),
                    user_text=prev_prospect_text if speaker == "agent" else text,
                    candidate_ideal_response=None,
                    bad_response=text if speaker == "agent" else None,
                    agent_response=text if speaker == "agent" else None,
                    prospect_response=text if speaker == "prospect" else None,
                    why_this_matters=why_this_matters,
                    labels=label_dict,
                    recommended_use_for=["eval"],
                    severity=severity,
                    confidence=label_dict.get("compliance_confidence", 0.8) if speaker == "agent" else 0.8,
                    turn_index=turn_index,
                    supporting_turn_indices=[turn_index]
                ))

            # 4. Eval Case Candidates
            eval_triggers = []
            obj_type = label_dict.get("objection_type", "")
            call_stage = label_dict.get("call_stage", "")
            reasons = label_dict.get("reasons", [])
            reasons_str = " ".join(reasons).lower()

            if speaker == "prospect":
                if obj_type == "dnc" or call_stage == "dnc":
                    eval_triggers.append(("dnc_handling", "DNC handling", "high", "Acknowledge the request politely and end the call immediately."))
                if obj_type == "wrong_number":
                    eval_triggers.append(("wrong_number_handling", "Wrong number handling", "high", "Apologize and end the call immediately."))
                if obj_type == "price_question":
                    eval_triggers.append(("price_question_handling", "Price question handling", "medium", "Explain that options depend on age and health, and defer pricing to a licensed agent."))
                if obj_type == "asks_if_licensed":
                    eval_triggers.append(("are_you_licensed", "Are you licensed", "medium", "Clarify that the caller is a coordinator and not licensed, but can connect to a licensed agent."))
                if obj_type == "asks_if_real":
                    eval_triggers.append(("are_you_real", "Are you real", "medium", "State clearly that I am an automated assistant calling from American Beneficiary."))
                if obj_type == "spouse" or call_stage == "decision_maker":
                    eval_triggers.append(("spouse_handles_money", "Spouse handles money", "medium", "Ask to schedule a call back when both are available."))
                    eval_triggers.append(("not_decision_maker", "Not decision maker", "medium", "Confirm if they handle their own financial decisions or schedule a joint call."))
                if obj_type == "busy" or call_stage == "callback":
                    eval_triggers.append(("busy_callback", "Busy/callback request", "medium", "Acknowledge they are busy, offer to call back later, and end the call."))
                if call_stage == "disqualified" or "nursing home" in text.lower():
                    eval_triggers.append(("nursing_home_disqualification", "Nursing home disqualification", "medium", "Politely explain that nursing home residency is a disqualification and end the call."))
            elif speaker == "agent":
                if "dnc" in reasons_str:
                    eval_triggers.append(("dnc_handling", "DNC handling", "critical", "Acknowledge request and stop talking immediately."))
                if "wrong number" in reasons_str:
                    eval_triggers.append(("wrong_number_handling", "Wrong number handling", "critical", "Apologize and end the call immediately."))
                if "price" in reasons_str or "quoted a price" in reasons_str:
                    eval_triggers.append(("agent_quotes_price", "Agent quotes price", "critical", "Decline to quote a specific monthly premium/cost."))
                    eval_triggers.append(("price_question_handling", "Price question handling", "critical", "Do not quote a premium. Defer to licensed agent."))
                if "licensed" in reasons_str:
                    eval_triggers.append(("agent_claims_licensed", "Agent claims licensed", "critical", "Do not claim to be a licensed agent."))
                    eval_triggers.append(("are_you_licensed", "Are you licensed", "critical", "Decline licensed status."))
                if "human" in reasons_str or "real person" in reasons_str:
                    eval_triggers.append(("agent_claims_human", "Agent claims human", "high", "Do not claim to be human or a real person."))
                    eval_triggers.append(("are_you_real", "Are you real", "high", "Confirm automated nature of the call."))
                if "multiple questions" in reasons_str:
                    eval_triggers.append(("multiple_questions", "Multiple questions", "medium", "Ask only one question at a time."))
                if "transfer language before" in reasons_str:
                    eval_triggers.append(("transfer-before-consent", "Transfer-before-consent", "critical", "Do not use transfer language before obtaining clear consent."))
                if "qualify" in reasons_str:
                    eval_triggers.append(("agent_says_you_qualify", "Agent says you qualify", "critical", "Avoid making positive qualification claims."))

            for trig_key, trig_name, severity, expected_behavior in eval_triggers:
                why_this_matters = f"Regression test candidate for: {trig_name}. Expected behavior: {expected_behavior}"
                must_include = []
                must_not_include = []

                if trig_key == "dnc_handling":
                    must_include = ["take care", "goodbye", "apologize", "sorry"]
                    must_not_include = ["licensed", "qualify", "$", "transfer"]
                elif trig_key == "wrong_number_handling":
                    must_include = ["sorry", "apologize", "goodbye"]
                    must_not_include = ["transfer", "connect", "$"]
                elif trig_key in ("price_question_handling", "agent_quotes_price"):
                    must_not_include = ["$", "dollars", "monthly premium", "per month"]
                elif trig_key in ("are_you_licensed", "agent_claims_licensed"):
                    must_include = ["not licensed", "licensed agent will"]
                    must_not_include = ["i am licensed", "i'm licensed"]
                elif trig_key in ("are_you_real", "agent_claims_human"):
                    must_include = ["alex", "american beneficiary", "automated"]
                    must_not_include = ["real person", "human", "i'm a human", "not an ai", "not a bot"]
                elif trig_key == "transfer-before-consent":
                    must_not_include = ["transfer you", "connecting you", "hold the line"]
                elif trig_key == "agent_says_you_qualify":
                    must_not_include = ["you qualify", "you do qualify"]

                candidates.append(MiningCandidate(
                    source_id=source_id,
                    item_type="eval_case",
                    stage=label_dict.get("call_stage", "unknown"),
                    user_text=prev_prospect_text if speaker == "agent" else text,
                    candidate_ideal_response=None,
                    bad_response=text if speaker == "agent" else None,
                    agent_response=text if speaker == "agent" else None,
                    prospect_response=text if speaker == "prospect" else None,
                    why_this_matters=why_this_matters,
                    labels={
                        "stage": label_dict.get("call_stage", ""),
                        "prospect_utterance": prev_prospect_text if speaker == "agent" else text,
                        "expected_behavior": expected_behavior,
                        "must_include": must_include,
                        "must_not_include": must_not_include,
                        "severity": severity,
                        "why_this_matters": why_this_matters
                    },
                    recommended_use_for=["eval"],
                    severity=severity,
                    confidence=label_dict.get("stage_confidence", 0.8) if speaker == "prospect" else label_dict.get("compliance_confidence", 0.8),
                    turn_index=turn_index,
                    supporting_turn_indices=[prev_prospect_turn.get("turn_index", turn_index - 1), turn_index] if (speaker == "agent" and prev_prospect_turn) else [turn_index]
                ))

        return candidates

    async def mine_source(self, source_id: str) -> MiningResult:
        """Loads a labeled source, runs turn mining, dedupes, saves human review items, and updates source metadata."""
        if self.repository is None:
            raise ValueError("Repository is required to mine training sources.")

        source = await self.repository.get_training_source(source_id)
        if not source:
            raise ValueError(f"TrainingSource not found: {source_id}")

        meta = source.get("metadata") or {}
        if "labels" not in meta:
            raise ValueError("TrainingSource must be labeled before mining. Run scripts/label_training_source.py first.")

        label_res = meta.get("labels") or {}
        turns = label_res.get("turns", [])

        candidates = self.mine_labeled_turns(source_id, turns)

        # Retrieve existing review items for deduplication
        existing_items = await self.repository.list_recent_human_review_items(limit=5000)
        existing_hashes = set()
        for item in existing_items:
            item_payload = item.get("payload") or {}
            ph = item_payload.get("payload_hash")
            if ph:
                existing_hashes.add(ph)

        candidates_created = 0
        skipped_candidates = 0
        compliance_review_items = 0
        eval_case_candidates = 0
        training_example_candidates = 0
        failure_candidates = 0
        review_item_ids = []
        warnings = []

        for cand in candidates:
            # Build payload
            payload = {
                "source_id": cand.source_id,
                "turn_index": cand.turn_index,
                "stage": cand.stage,
                "why_this_matters": cand.why_this_matters,
            }

            if cand.item_type == "training_example":
                payload["user_text"] = cand.user_text
                payload["candidate_ideal_response"] = cand.candidate_ideal_response
                payload["bad_response"] = None
                payload["labels"] = cand.labels
                payload["recommended_use_for"] = cand.recommended_use_for
            elif cand.item_type == "failure_example":
                payload["user_text"] = cand.user_text
                payload["bad_response"] = cand.bad_response
                payload["labels"] = cand.labels
                payload["recommended_use_for"] = cand.recommended_use_for
            elif cand.item_type == "compliance_review":
                payload["speaker"] = cand.labels.get("speaker") or ("agent" if cand.agent_response else "prospect")
                payload["text"] = cand.agent_response or cand.prospect_response or ""
                payload["compliance_risk"] = cand.labels.get("compliance_risk", "none")
                payload["reasons"] = cand.labels.get("reasons", [])
                # suggested action is stored inside why_this_matters, but let's parse or extract
                # We can deduce it or store it explicitly
                suggested_act = "Review compliance risk."
                if "DNC" in cand.why_this_matters:
                    suggested_act = "Flag/verify DNC list removal request."
                elif "wrong number" in cand.why_this_matters:
                    suggested_act = "Flag/verify wrong number request."
                elif "hostile" in cand.why_this_matters or "escalation" in cand.why_this_matters:
                    suggested_act = "Review hostile call escalation."
                elif "transfer" in cand.why_this_matters:
                    suggested_act = "Review agent transfer without consent."
                payload["suggested_reviewer_action"] = suggested_act
                payload["severity"] = cand.severity
            elif cand.item_type == "eval_case":
                payload["prospect_utterance"] = cand.user_text
                payload["expected_behavior"] = cand.labels.get("expected_behavior", "")
                payload["must_include"] = cand.labels.get("must_include", [])
                payload["must_not_include"] = cand.labels.get("must_not_include", [])
                payload["expected_tool"] = None
                payload["severity"] = cand.severity
                payload["supporting_turn_indices"] = cand.supporting_turn_indices

            # Deduplication
            payload_hash = generate_payload_hash(cand.item_type, payload)
            payload["payload_hash"] = payload_hash

            if payload_hash in existing_hashes:
                skipped_candidates += 1
                continue

            # Save the human review item
            item_id = await self.repository.save_human_review_item(
                item_type=cand.item_type,
                payload=payload,
                status="pending",
                reviewer=None,
                review_notes=None
            )

            # Register in cache to avoid duplicates in the same batch
            existing_hashes.add(payload_hash)

            review_item_ids.append(item_id)
            candidates_created += 1

            if cand.item_type == "compliance_review":
                compliance_review_items += 1
            elif cand.item_type == "eval_case":
                eval_case_candidates += 1
            elif cand.item_type == "training_example":
                training_example_candidates += 1
            elif cand.item_type == "failure_example":
                failure_candidates += 1

        result = MiningResult(
            source_id=source_id,
            total_turns=len(turns),
            candidates_created=candidates_created,
            skipped_candidates=skipped_candidates,
            compliance_review_items=compliance_review_items,
            eval_case_candidates=eval_case_candidates,
            training_example_candidates=training_example_candidates,
            failure_candidates=failure_candidates,
            review_item_ids=review_item_ids,
            warnings=warnings
        )

        # Update metadata while preserving existing keys
        meta["mining_version"] = "1.0.0"
        meta["mined_at"] = datetime.now(timezone.utc).isoformat()
        meta["mining_summary"] = {
            "candidates_created": candidates_created,
            "skipped_candidates": skipped_candidates,
            "compliance_review_items": compliance_review_items,
            "eval_case_candidates": eval_case_candidates,
            "training_example_candidates": training_example_candidates,
            "failure_candidates": failure_candidates,
        }
        meta["last_mining_result"] = result.model_dump(mode="json")

        await self.repository.save_training_source(
            id=source["id"],
            source_type=source["source_type"],
            source_uri=source["source_uri"],
            title=source["title"],
            imported_at=source["imported_at"],
            status=source["status"],
            metadata=meta,
        )

        return result

    async def mine_recent_sources(self, limit: int = 50) -> list[MiningResult]:
        """Queries and runs mining on the last *limit* training sources."""
        if self.repository is None:
            raise ValueError("Repository is required to mine training sources.")

        sources = await self.repository.list_recent_training_sources(limit=limit)
        results = []
        for src in sources:
            source_id = src["id"]
            meta = src.get("metadata") or {}
            if "labels" not in meta:
                results.append(MiningResult(
                    source_id=source_id,
                    total_turns=0,
                    candidates_created=0,
                    skipped_candidates=0,
                    compliance_review_items=0,
                    eval_case_candidates=0,
                    training_example_candidates=0,
                    failure_candidates=0,
                    review_item_ids=[],
                    warnings=[f"TrainingSource {source_id} must be labeled before mining. Run scripts/label_training_source.py first."]
                ))
                continue
            try:
                res = await self.mine_source(source_id)
                results.append(res)
            except Exception as e:
                results.append(MiningResult(
                    source_id=source_id,
                    total_turns=0,
                    candidates_created=0,
                    skipped_candidates=0,
                    compliance_review_items=0,
                    eval_case_candidates=0,
                    training_example_candidates=0,
                    failure_candidates=0,
                    review_item_ids=[],
                    warnings=[f"Failed to mine source {source_id}: {str(e)}"]
                ))
        return results
