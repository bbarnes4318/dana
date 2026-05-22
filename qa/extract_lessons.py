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
    "qualification_completion": (
        "Collect more qualification data: the agent should naturally gather age, state, "
        "phone type, budget confirmation, and interest level before attempting to transfer."
    ),
    "objection_handling": (
        "Strengthen objection handling: when the prospect objects, acknowledge their "
        "concern empathetically (e.g., 'I completely understand'), then pivot with a "
        "concise benefit statement. Avoid ignoring objections or giving one-word responses."
    ),
    "compliance_safety": (
        "Fix compliance violations: the agent must NEVER quote premiums, guarantee "
        "acceptance or approval, or make coverage promises. All forbidden phrases must "
        "be removed from responses."
    ),
    "transfer_readiness": (
        "Improve transfer timing: only transfer when the prospect is fully qualified "
        "(age, state, phone type, and budget/interest confirmed). Do not transfer too "
        "early or miss transfer opportunities when the prospect is ready."
    ),
    "talk_listen_balance": (
        "Reduce agent verbosity: keep agent turns under 60 words, ask only one question "
        "per turn, and let the prospect speak more. Aim for a 40-55%% agent talk ratio."
    ),
    "close_probability": (
        "Increase close probability: build rapport, match the prospect's energy level, "
        "and ensure strong interest signals before transferring. When interest is low, "
        "use soft benefit statements to raise engagement."
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
