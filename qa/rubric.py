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
    # Criterion definitions
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

    QUALIFICATION_COMPLETION = ScoringCriterion(
        name="qualification_completion",
        weight=2.0,
        description="How many required qualification fields were collected from the prospect.",
        scoring_guide=(
            "0: No fields collected. "
            "5: Half of required fields collected. "
            "10: All required fields collected."
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
        description="Transferred the call at the appropriate time — not too early or too late.",
        scoring_guide=(
            "0: Transferred with no qualification or refused when ready. "
            "5: Transferred slightly early or waited one stage too long. "
            "10: Transferred at exactly the right qualification point."
        ),
    )

    TALK_LISTEN_BALANCE = ScoringCriterion(
        name="talk_listen_balance",
        weight=1.0,
        description="Agent did not dominate the conversation; balanced talk-listen ratio.",
        scoring_guide=(
            "0: Agent spoke >80%% of total words. "
            "5: Agent spoke ~65%% of total words. "
            "10: Agent spoke 40-55%% of total words."
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
    # All criteria in evaluation order
    # ------------------------------------------------------------------

    ALL_CRITERIA: list[ScoringCriterion] = [
        OPENING_STRENGTH,
        HUMAN_REALISM,
        QUALIFICATION_COMPLETION,
        OBJECTION_HANDLING,
        COMPLIANCE_SAFETY,
        TRANSFER_READINESS,
        TALK_LISTEN_BALANCE,
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
