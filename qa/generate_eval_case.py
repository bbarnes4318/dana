"""Eval case generation from real calls.

Converts a scored call record into an eval scenario YAML dict that can be
saved to ``evals/scenarios/`` and replayed by the eval runner.
"""

from __future__ import annotations

from qa.call_record import CallRecord
from qa.scoring import QAScorecard


# Maps detected issue keywords to eval assertion types
_ISSUE_TO_ASSERTION: dict[str, dict] = {
    "compliance risk": {
        "type": "no_forbidden_phrase",
        "expected": True,
    },
    "transferred too early": {
        "type": "transfer_only_when_ready",
        "expected": True,
    },
    "failed to transfer when ready": {
        "type": "transfer_only_when_ready",
        "expected": True,
    },
    "asked too many questions": {
        "type": "one_question_max",
        "expected": True,
    },
    "talked too long": {
        "type": "response_under_word_limit",
        "expected": True,
        "params": {"max_words": 60},
    },
    "bad opening": {
        "type": "correct_next_stage",
        "expected": "permission",
    },
}


class EvalCaseGenerator:
    """Generates eval scenario dicts from real call records and scorecards.

    The output dict conforms to the :class:`evals.scenario_schema.EvalScenario`
    schema and can be serialised to YAML for replay.
    """

    def generate_from_call(
        self,
        record: CallRecord,
        scorecard: QAScorecard,
    ) -> dict:
        """Create an eval scenario YAML dict from a real call.

        Parameters:
            record: The completed call record.
            scorecard: The QA scorecard for this call.

        Returns:
            A dict suitable for ``yaml.dump()`` that conforms to the
            ``EvalScenario`` schema.
        """
        # Build turns list
        turns = [
            {"speaker": t.speaker, "text": t.text}
            for t in record.turns
        ]

        # Build assertions from detected issues
        assertions = self._build_assertions(scorecard)

        # If no issues, add a basic compliance assertion
        if not assertions:
            assertions.append({
                "type": "no_forbidden_phrase",
                "expected": True,
            })

        # Determine initial stage from first turn
        initial_stage = record.turns[0].stage if record.turns else "opening"

        # Build tags from grade and issues
        tags = [f"grade:{scorecard.grade}", f"score:{scorecard.overall_score}"]
        if scorecard.issues:
            tags.append("has_issues")
        if scorecard.overall_score >= 8.0:
            tags.append("high_quality")
        if scorecard.overall_score < 5.0:
            tags.append("needs_improvement")

        # Determine prospect persona from lead profile
        profile = record.lead_profile
        age = profile.get("age", "unknown")
        state = profile.get("state", "unknown")
        interest = profile.get("interest_level", "unknown")
        persona = f"Prospect age {age} from {state}, interest level: {interest}"

        return {
            "name": f"replay_{record.call_id[:8]}",
            "description": (
                f"Auto-generated eval from call {record.call_id}. "
                f"Grade: {scorecard.grade}, Score: {scorecard.overall_score}/10. "
                f"Issues: {len(scorecard.issues)}."
            ),
            "prospect_persona": persona,
            "initial_stage": initial_stage,
            "turns": turns,
            "assertions": assertions,
            "expected_final_stage": record.final_stage,
            "tags": tags,
        }

    def _build_assertions(self, scorecard: QAScorecard) -> list[dict]:
        """Build eval assertions from scorecard issues."""
        assertions: list[dict] = []
        seen_types: set[str] = set()

        for issue in scorecard.issues:
            issue_lower = issue.lower()
            for keyword, assertion in _ISSUE_TO_ASSERTION.items():
                if keyword in issue_lower and assertion["type"] not in seen_types:
                    assertions.append(dict(assertion))
                    seen_types.add(assertion["type"])

        return assertions
