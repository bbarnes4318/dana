"""Lesson extraction from QA scorecards.

Generates plain-text improvement recommendations from low-scoring
areas so the prompt or agent behaviour can be tuned.
"""

from __future__ import annotations

from qa.call_record import CallRecord
from qa.rubric import QARubric
from qa.scoring import QAScorecard


# ------------------------------------------------------------------
# Per-criterion lesson templates
# ------------------------------------------------------------------

_LESSON_TEMPLATES: dict[str, str] = {
    "opening_strength": (
        "Improve the opening: ensure the agent greets warmly, states their name, "
        "the company name, and gives a clear reason for the call in the very first turn."
    ),
    "human_realism": (
        "Remove robotic language: eliminate chatbot phrases like 'Sure!', 'Absolutely!', "
        "'Great question!', and never output markdown formatting, bullet points, or "
        "numbered lists in spoken dialogue."
    ),
    "short_flow_completion": (
        "Complete short-flow qualification: ensure all five required fields "
        "(open_to_review, age_range_confirmed, living_independently, financial_decision_maker, "
        "transfer_consent_confirmed) are gathered and confirmed."
    ),
    "objection_handling": (
        "Strengthen objection handling: when the prospect objects, acknowledge their "
        "concern empathetically (e.g., 'I completely understand'), then pivot with a "
        "concise benefit statement. Avoid ignoring objections or giving one-word responses."
    ),
    "compliance_safety": (
        "Fix compliance violations: the agent must NEVER quote premiums, guarantee "
        "acceptance or approval, make coverage promises, claim to be licensed, or claim to be human."
    ),
    "transfer_readiness": (
        "Improve transfer timing: only transfer when the prospect has confirmed all "
        "five short-flow fields. Do not transfer early or miss transfer opportunities."
    ),
    "dnc_handling": (
        "Improve DNC/wrong-number handling: immediately stop the call when DNC or "
        "wrong-number is requested. Do not attempt to salvage or ask more questions."
    ),
    "disqualification_confirmation": (
        "Confirm disqualifiers: always confirm ambiguous disqualifying details "
        "(under 40, over 85, nursing home, assisted living, non-decision maker, no consent) "
        "with 'Just so I make sure I heard you right...' before ending the call."
    ),
    "talk_listen_balance": (
        "Improve talk/listen balance: do not dominate the conversation, and ask at "
        "most one question per agent turn."
    ),
    "latency_readiness": (
        "Ensure prompt responses and prevent agent turns after terminal stages are reached."
    ),
    "close_probability": (
        "Increase close probability: build rapport, match the prospect's energy level, "
        "and ensure strong interest signals before transferring."
    ),
}


class LessonExtractor:
    """Extracts improvement lessons from a scored call.

    Analyses which criteria scored below a threshold and generates
    actionable plain-text recommendations.
    """

    def __init__(self, low_score_threshold: float = 6.0) -> None:
        """
        Args:
            low_score_threshold: Criterion scores at or below this value
                trigger a lesson. Defaults to 6.0.
        """
        self._threshold = low_score_threshold

    def extract_from_scorecard(
        self,
        scorecard: QAScorecard,
        record: CallRecord,
    ) -> list[str]:
        """Generate improvement lessons from a scorecard.

        Parameters:
            scorecard: The completed scorecard to analyse.
            record: The underlying call record (used for contextual details).

        Returns:
            A list of plain-text lesson strings, one per low-scoring area.
        """
        lessons: list[str] = []

        for criterion in QARubric.ALL_CRITERIA:
            score = scorecard.scores.get(criterion.name)
            if score is None:
                continue
            if score <= self._threshold:
                template = _LESSON_TEMPLATES.get(criterion.name)
                if template:
                    lessons.append(
                        f"[{criterion.name} — scored {score}/10] {template}"
                    )

        # Add issue-specific lessons
        for issue in scorecard.issues:
            issue_lower = issue.lower()
            if "missed objection" in issue_lower:
                lessons.append(
                    "Specific issue: one or more prospect objections went unanswered. "
                    "Always respond to objections before continuing to the next question."
                )
            if "compliance risk" in issue_lower:
                lessons.append(
                    "Specific issue: forbidden compliance phrases were used. "
                    "Review and remove all prohibited language from agent prompts."
                )
            if "transferred too early" in issue_lower:
                lessons.append(
                    "Specific issue: the agent transferred the call before the "
                    "prospect was fully qualified. Ensure all required fields are "
                    "collected before initiating transfer."
                )

        # De-duplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for lesson in lessons:
            if lesson not in seen:
                seen.add(lesson)
                unique.append(lesson)

        return unique
