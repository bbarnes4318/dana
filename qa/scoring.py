"""Call scoring engine for Dana QA pipeline.

Provides :class:`CallScorer` which evaluates a :class:`CallRecord` against
the :class:`QARubric` and produces a :class:`QAScorecard`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from qa.call_record import CallRecord
from qa.rubric import QARubric


# ------------------------------------------------------------------
# Compliance patterns — phrases the agent must NEVER say
# ------------------------------------------------------------------

_FORBIDDEN_PHRASES: list[str] = [
    "guaranteed acceptance",
    "guaranteed approval",
    "you're approved",
    "you are approved",
    "no health questions",
    "your premium will be",
    "your rate is",
    "your monthly cost",
    "I can guarantee",
    "we guarantee",
    "guaranteed issue",
]

_CHATBOT_PHRASES: list[str] = [
    "as an ai",
    "as a language model",
    "i'm an ai",
    "i am an ai",
    "i don't have feelings",
    "i cannot feel",
    "sure!",
    "absolutely!",
    "great question!",
    "that's a great question",
    "no problem!",
    "of course!",
    "certainly!",
    "here are some",
    "here is a list",
]

_MARKDOWN_PATTERN = re.compile(r"(\*\*|__|##|```|^\s*[-*]\s)", re.MULTILINE)


# ------------------------------------------------------------------
# Scorecard
# ------------------------------------------------------------------


@dataclass
class QAScorecard:
    """Result of scoring a single call.

    Attributes:
        call_id: Identifier of the scored call.
        scores: Per-criterion scores (name -> 0-10 float).
        issues: Human-readable issue descriptions detected.
        overall_score: Weighted average of all criterion scores.
        grade: Letter grade A–F.
    """

    call_id: str
    scores: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    overall_score: float = 0.0
    grade: str = "F"


def _score_to_grade(score: float) -> str:
    """Map a 0-10 score to a letter grade."""
    if score >= 9.0:
        return "A"
    if score >= 8.0:
        return "B"
    if score >= 7.0:
        return "C"
    if score >= 5.0:
        return "D"
    return "F"


# ------------------------------------------------------------------
# Scorer
# ------------------------------------------------------------------


class CallScorer:
    """Evaluates a :class:`CallRecord` and produces a :class:`QAScorecard`.

    All scoring is rule-based (no LLM calls) so it can run synchronously
    inside CI or a post-call webhook.
    """

    def score_call(self, record: CallRecord) -> QAScorecard:
        """Score every criterion and return a completed scorecard."""
        scores: dict[str, float] = {}
        issues: list[str] = []

        scores["opening_strength"] = self._score_opening(record, issues)
        scores["human_realism"] = self._score_realism(record, issues)
        scores["qualification_completion"] = self._score_qualification(record, issues)
        scores["objection_handling"] = self._score_objection_handling(record, issues)
        scores["compliance_safety"] = self._score_compliance(record, issues)
        scores["transfer_readiness"] = self._score_transfer(record, issues)
        scores["talk_listen_balance"] = self._score_balance(record, issues)
        scores["close_probability"] = self._score_close_probability(record, issues)

        overall = QARubric.compute_overall(scores)

        return QAScorecard(
            call_id=record.call_id,
            scores=scores,
            issues=issues,
            overall_score=overall,
            grade=_score_to_grade(overall),
        )

    # ------------------------------------------------------------------
    # Individual criterion scorers
    # ------------------------------------------------------------------

    def _score_opening(self, record: CallRecord, issues: list[str]) -> float:
        """Check first agent turn for a warm introduction."""
        agent_turns = record.agent_turns
        if not agent_turns:
            issues.append("bad opening: no agent turns found")
            return 0.0

        first = agent_turns[0].text.lower()

        # Expect: agent name, company-ish word, and a reason/calling indicator
        has_greeting = any(
            g in first for g in ("hi", "hello", "hey", "good morning", "good afternoon", "good evening")
        )
        has_name = any(
            n in first for n in ("my name is", "this is", "i'm")
        )
        has_reason = any(
            r in first for r in ("calling", "reaching out", "reason", "follow up", "following up")
        )

        score = 0.0
        if has_greeting:
            score += 3.0
        if has_name:
            score += 4.0
        if has_reason:
            score += 3.0

        if score < 5.0:
            issues.append("bad opening: missing greeting, name, or reason for call")

        return min(score, 10.0)

    def _score_realism(self, record: CallRecord, issues: list[str]) -> float:
        """Check for chatbot phrases, markdown, and bullet points."""
        agent_texts = [t.text for t in record.agent_turns]
        all_text = " ".join(agent_texts).lower()

        deductions = 0.0

        # Chatbot phrases
        for phrase in _CHATBOT_PHRASES:
            if phrase in all_text:
                deductions += 2.0

        # Markdown formatting
        if _MARKDOWN_PATTERN.search(" ".join(agent_texts)):
            deductions += 3.0

        if deductions > 0:
            issues.append(
                "human realism: detected chatbot phrases or markdown formatting"
            )

        return max(10.0 - deductions, 0.0)

    def _score_qualification(self, record: CallRecord, issues: list[str]) -> float:
        """Score based on how many lead profile fields were collected."""
        profile = record.lead_profile
        tracked_fields = [
            "first_name", "last_name", "age", "state", "phone_type",
            "can_receive_text", "budget_confirmed", "has_existing_coverage",
            "beneficiary_or_family_reason", "interest_level",
        ]

        filled = sum(
            1 for f in tracked_fields
            if profile.get(f) is not None
        )
        ratio = filled / len(tracked_fields)
        return round(ratio * 10.0, 1)

    def _score_objection_handling(self, record: CallRecord, issues: list[str]) -> float:
        """Check if objections were detected and responded to."""
        objection_stages = [
            i for i, t in enumerate(record.turns)
            if t.stage == "objection" and t.speaker == "prospect"
        ]

        if not objection_stages:
            # No objections — score as neutral good
            return 8.0

        # For each objection, check that the next agent turn exists
        handled = 0
        missed = 0
        weak = 0
        for idx in objection_stages:
            # Find the next agent turn after this objection
            following_agent = [
                t for t in record.turns[idx + 1:]
                if t.speaker == "agent"
            ]
            if not following_agent:
                missed += 1
                continue

            response = following_agent[0].text.lower()
            # A decent rebuttal should be >10 words and empathetic
            word_count = len(response.split())
            has_empathy = any(
                e in response
                for e in ("understand", "hear you", "appreciate", "makes sense", "i get that", "totally")
            )

            if word_count < 8 or not has_empathy:
                weak += 1
            else:
                handled += 1

        total = len(objection_stages)
        if missed > 0:
            issues.append(f"missed objection: {missed} objection(s) had no agent response")
        if weak > 0:
            issues.append(f"weak rebuttal: {weak} objection response(s) lacked empathy or substance")

        score = (handled / total) * 10.0 if total > 0 else 8.0
        return round(min(score, 10.0), 1)

    def _score_compliance(self, record: CallRecord, issues: list[str]) -> float:
        """Run compliance filter on all agent turns."""
        agent_texts = [t.text.lower() for t in record.agent_turns]
        violations: list[str] = []

        for text in agent_texts:
            for phrase in _FORBIDDEN_PHRASES:
                if phrase in text:
                    violations.append(phrase)

        if violations:
            unique = list(set(violations))
            issues.append(
                f"compliance risk: forbidden phrase(s) detected — {', '.join(unique)}"
            )
            # Each unique violation costs 3 points
            return max(10.0 - len(unique) * 3.0, 0.0)

        return 10.0

    def _score_transfer(self, record: CallRecord, issues: list[str]) -> float:
        """Check if transfer was at appropriate timing."""
        profile = record.lead_profile
        outcome = record.outcome

        # Required fields for transfer readiness
        required_for_transfer = ["age", "state", "phone_type"]
        fields_present = sum(
            1 for f in required_for_transfer
            if profile.get(f) is not None
        )
        has_budget_or_interest = (
            profile.get("budget_confirmed") is True
            or profile.get("interest_level") == "high"
        )

        fully_qualified = fields_present == len(required_for_transfer) and has_budget_or_interest

        if outcome == "transferred":
            if fully_qualified:
                return 10.0
            else:
                issues.append("transferred too early: prospect was not fully qualified")
                return 3.0

        # Not transferred — did we miss an opportunity?
        if fully_qualified and outcome in ("ended", "abandoned"):
            issues.append("failed to transfer when ready: prospect was qualified but not transferred")
            return 3.0

        # Other outcomes (callback, dnc, disqualified) — transfer wasn't expected
        if outcome in ("callback", "dnc", "disqualified"):
            return 8.0

        # Ended without qualification — neutral
        return 6.0

    def _score_balance(self, record: CallRecord, issues: list[str]) -> float:
        """Measure agent vs prospect word counts."""
        agent_words = record.agent_word_count
        prospect_words = record.prospect_word_count
        total = agent_words + prospect_words

        if total == 0:
            return 5.0

        agent_ratio = agent_words / total

        # Also check for individual turns that are too long
        long_turns = [
            t for t in record.agent_turns
            if len(t.text.split()) > 60
        ]
        if long_turns:
            issues.append(
                f"talked too long: {len(long_turns)} agent turn(s) exceeded 60 words"
            )

        # Check for asking too many questions in a single turn
        for turn in record.agent_turns:
            question_count = turn.text.count("?")
            if question_count > 1:
                issues.append(
                    f"asked too many questions: agent asked {question_count} questions in one turn"
                )
                break  # Report once

        # Ideal ratio: 40-55% agent
        if 0.40 <= agent_ratio <= 0.55:
            return 10.0
        elif 0.35 <= agent_ratio <= 0.65:
            return 7.0
        elif 0.25 <= agent_ratio <= 0.75:
            return 5.0
        else:
            return 2.0

    def _score_close_probability(self, record: CallRecord, issues: list[str]) -> float:
        """Heuristic based on interest level and qualification completeness."""
        profile = record.lead_profile
        interest = profile.get("interest_level", "none")
        outcome = record.outcome

        # Base score from outcome
        outcome_scores = {
            "transferred": 9.0,
            "callback": 6.0,
            "dnc": 1.0,
            "disqualified": 2.0,
            "abandoned": 2.0,
            "ended": 4.0,
        }
        base = outcome_scores.get(outcome, 4.0)

        # Adjust by interest level
        interest_bonus = {
            "high": 1.0,
            "medium": 0.0,
            "low": -1.0,
            "none": -2.0,
        }
        bonus = interest_bonus.get(str(interest), 0.0)

        # Check if disqualifier was confirmed
        if profile.get("disqualified_reason") and outcome != "disqualified":
            issues.append(
                "did not confirm disqualifier: disqualified_reason set but outcome is not 'disqualified'"
            )

        return round(min(max(base + bonus, 0.0), 10.0), 1)
