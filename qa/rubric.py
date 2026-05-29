"""QA rubric definition for Dana call quality scoring.

Defines the criteria, weights, and scoring guides used by the
:class:`qa.scoring.CallScorer` to evaluate call recordings.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoringCriterion:
    """A single criterion within the QA rubric.

    Attributes:
        name: Machine-readable identifier.
        weight: Relative weight in the overall score (weights are normalised).
        description: Human-readable explanation of what this criterion measures.
        scoring_guide: Description of what constitutes a 0, 5, and 10.
    """

    name: str
    weight: float
    description: str
    scoring_guide: str


class QARubric:
    """QA rubric containing all scoring criteria for a Dana call.

    Each criterion is scored 0–10.  The ``overall_score`` is a weighted
    average of the individual criterion scores.
    """

    # ------------------------------------------------------------------
    # Criterion definitions (Exactly 11 categories)
    # ------------------------------------------------------------------

    OPENING_STRENGTH = ScoringCriterion(
        name="opening_strength",
        weight=1.0,
        description="Clear introduction with agent name, company, and reason for call.",
        scoring_guide=(
            "0: No introduction or completely wrong opener. "
            "5: Introduced but missed company or reason. "
            "10: Warm, clear intro with name, company, and reason."
        ),
    )

    HUMAN_REALISM = ScoringCriterion(
        name="human_realism",
        weight=1.5,
        description="Natural, human-sounding speech free of robotic phrases, markdown, or bullet points.",
        scoring_guide=(
            "0: Obvious chatbot language, markdown formatting, or bullet lists. "
            "5: Mostly natural but occasional stiff phrasing. "
            "10: Indistinguishable from a skilled human agent."
        ),
    )

    SHORT_FLOW_COMPLETION = ScoringCriterion(
        name="short_flow_completion",
        weight=2.0,
        description="How many required short-flow qualification fields were confirmed (open_to_review, age_range_confirmed, living_independently, financial_decision_maker, transfer_consent_confirmed).",
        scoring_guide=(
            "0: None of the fields confirmed. "
            "5: Partial confirmation of fields. "
            "10: All 5 short-flow qualification fields confirmed."
        ),
    )

    OBJECTION_HANDLING = ScoringCriterion(
        name="objection_handling",
        weight=1.5,
        description="Appropriate, empathetic responses to prospect objections.",
        scoring_guide=(
            "0: Ignored objections or gave inappropriate responses. "
            "5: Acknowledged objections but weak rebuttals. "
            "10: Empathetic acknowledgment with strong, on-script rebuttal."
        ),
    )

    COMPLIANCE_SAFETY = ScoringCriterion(
        name="compliance_safety",
        weight=3.0,
        description="No forbidden claims, no premium quotes, no approval promises.",
        scoring_guide=(
            "0: Multiple compliance violations found. "
            "5: Minor borderline language detected. "
            "10: Fully compliant — no forbidden phrases or claims."
        ),
    )

    TRANSFER_READINESS = ScoringCriterion(
        name="transfer_readiness",
        weight=1.5,
        description="Transferred the call at the appropriate time — only when all confirmations are obtained.",
        scoring_guide=(
            "0: Transferred prematurely or missed transfer opportunity. "
            "10: Transferred exactly when all short-flow conditions were ready."
        ),
    )

    DNC_HANDLING = ScoringCriterion(
        name="dnc_handling",
        weight=2.0,
        description="Empathy and immediate termination on DNC or wrong-number request.",
        scoring_guide=(
            "0: Kept selling or ignored DNC/wrong number request. "
            "10: Immediately stopped the call and marked status appropriately."
        ),
    )

    DISQUALIFICATION_CONFIRMATION = ScoringCriterion(
        name="disqualification_confirmation",
        weight=1.5,
        description="Confirmation of disqualifying facts before ending the call.",
        scoring_guide=(
            "0: Ended call instantly without double-checking the disqualifier. "
            "10: Double-checked disqualifier with the prospect before politely ending."
        ),
    )

    TALK_LISTEN_BALANCE = ScoringCriterion(
        name="talk_listen_balance",
        weight=1.0,
        description="Agent did not dominate conversation and asked at most one question per turn.",
        scoring_guide=(
            "0: Dominated call or asked multiple questions in one turn. "
            "10: Balanced talk/listen ratio and clean question pacing."
        ),
    )

    LATENCY_READINESS = ScoringCriterion(
        name="latency_readiness",
        weight=1.0,
        description="Prompt response times and no trailing agent turns after terminal stages.",
        scoring_guide=(
            "0: Had agent turns after a terminal stage or significant delay. "
            "10: Prompt response and zero activity after call ended."
        ),
    )

    CLOSE_PROBABILITY = ScoringCriterion(
        name="close_probability",
        weight=1.5,
        description="Heuristic likelihood that the call would result in a successful transfer/sale.",
        scoring_guide=(
            "0: Prospect was hostile or completely uninterested. "
            "5: Prospect was lukewarm — might convert with more work. "
            "10: Prospect was highly interested and fully qualified."
        ),
    )

    # ------------------------------------------------------------------
    # All criteria in evaluation order (Exactly 11 categories)
    # ------------------------------------------------------------------

    ALL_CRITERIA: list[ScoringCriterion] = [
        OPENING_STRENGTH,
        HUMAN_REALISM,
        SHORT_FLOW_COMPLETION,
        OBJECTION_HANDLING,
        COMPLIANCE_SAFETY,
        TRANSFER_READINESS,
        DNC_HANDLING,
        DISQUALIFICATION_CONFIRMATION,
        TALK_LISTEN_BALANCE,
        LATENCY_READINESS,
        CLOSE_PROBABILITY,
    ]

    @classmethod
    def criteria_by_name(cls) -> dict[str, ScoringCriterion]:
        """Return a dict mapping criterion name -> ScoringCriterion."""
        return {c.name: c for c in cls.ALL_CRITERIA}

    @classmethod
    def compute_overall(cls, scores: dict[str, float]) -> float:
        """Compute the weighted-average overall score from individual scores.

        Parameters:
            scores: Mapping of criterion name -> score (0-10).

        Returns:
            Weighted-average score, clamped to [0.0, 10.0].
        """
        total_weight = 0.0
        weighted_sum = 0.0
        criteria_map = cls.criteria_by_name()

        for name, score in scores.items():
            criterion = criteria_map.get(name)
            if criterion is None:
                continue
            weighted_sum += score * criterion.weight
            total_weight += criterion.weight

        if total_weight == 0.0:
            return 0.0

        overall = weighted_sum / total_weight
        return round(min(max(overall, 0.0), 10.0), 2)
