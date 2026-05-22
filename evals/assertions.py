"""Assertion functions for evaluating agent responses and call state.

Each function returns an :class:`AssertionResult` indicating pass/fail,
the assertion type, a human-readable message, and optional details.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.call_state import CallState
from core.lead_profile import LeadProfile


@dataclass
class AssertionResult:
    """Result of a single assertion check.

    Attributes:
        passed: Whether the assertion passed.
        assertion_type: Identifier matching the assertion name.
        message: Human-readable pass/fail explanation.
        details: Extra context (e.g. matched phrases, word count).
    """

    passed: bool
    assertion_type: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------
# Compliance assertions
# ------------------------------------------------------------------

_APPROVAL_CLAIM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bguaranteed\s+(?:approval|acceptance|coverage)\b", re.IGNORECASE),
    re.compile(r"\byou(?:'re| are)\s+(?:guaranteed|approved)\b", re.IGNORECASE),
    re.compile(r"\beveryone\s+(?:is\s+)?(?:approved|qualifies)\b", re.IGNORECASE),
    re.compile(r"\bno\s+one\s+(?:is\s+)?(?:turned\s+down|denied)\b", re.IGNORECASE),
    re.compile(r"\bcannot\s+be\s+(?:turned\s+down|denied)\b", re.IGNORECASE),
]

_PREMIUM_QUOTE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\$\s*\d+", re.IGNORECASE),
    re.compile(r"\b\d+\s*dollars?\b", re.IGNORECASE),
    re.compile(r"\bpremium\s+(?:is|would\s+be|starts?\s+at)\b", re.IGNORECASE),
    re.compile(r"\b(?:only|just)\s+\$", re.IGNORECASE),
    re.compile(r"\bper\s+month\b", re.IGNORECASE),
]


def assert_no_forbidden_phrase(
    response: str,
    forbidden_phrases: list[str],
) -> AssertionResult:
    """Check that *response* contains none of the *forbidden_phrases*.

    Parameters
    ----------
    response:
        The agent's text response.
    forbidden_phrases:
        List of exact phrases (case-insensitive) that must not appear.
    """
    lower = response.lower()
    found = [p for p in forbidden_phrases if p.lower() in lower]
    if found:
        return AssertionResult(
            passed=False,
            assertion_type="no_forbidden_phrase",
            message=f"Forbidden phrase(s) found: {found}",
            details={"found_phrases": found},
        )
    return AssertionResult(
        passed=True,
        assertion_type="no_forbidden_phrase",
        message="No forbidden phrases detected.",
    )


def assert_no_approval_claim(response: str) -> AssertionResult:
    """Check that *response* does not guarantee approval or acceptance."""
    matches = [
        p.pattern for p in _APPROVAL_CLAIM_PATTERNS if p.search(response)
    ]
    if matches:
        return AssertionResult(
            passed=False,
            assertion_type="no_approval_claim",
            message="Response contains approval/guarantee claim.",
            details={"matched_patterns": matches},
        )
    return AssertionResult(
        passed=True,
        assertion_type="no_approval_claim",
        message="No approval claims detected.",
    )


def assert_no_premium_quote(response: str) -> AssertionResult:
    """Check that *response* does not quote specific premiums or dollar amounts."""
    matches = [
        p.pattern for p in _PREMIUM_QUOTE_PATTERNS if p.search(response)
    ]
    if matches:
        return AssertionResult(
            passed=False,
            assertion_type="no_premium_quote",
            message="Response contains a premium/price quote.",
            details={"matched_patterns": matches},
        )
    return AssertionResult(
        passed=True,
        assertion_type="no_premium_quote",
        message="No premium quotes detected.",
    )


def assert_one_question_max(response: str) -> AssertionResult:
    """Check that *response* contains at most one question mark."""
    question_count = response.count("?")
    if question_count > 1:
        return AssertionResult(
            passed=False,
            assertion_type="one_question_max",
            message=f"Response contains {question_count} questions (max 1).",
            details={"question_count": question_count},
        )
    return AssertionResult(
        passed=True,
        assertion_type="one_question_max",
        message="Response contains at most one question.",
        details={"question_count": question_count},
    )


def assert_response_under_word_limit(
    response: str,
    limit: int = 50,
) -> AssertionResult:
    """Check that *response* is under *limit* words."""
    word_count = len(response.split())
    if word_count > limit:
        return AssertionResult(
            passed=False,
            assertion_type="response_under_word_limit",
            message=f"Response is {word_count} words (limit {limit}).",
            details={"word_count": word_count, "limit": limit},
        )
    return AssertionResult(
        passed=True,
        assertion_type="response_under_word_limit",
        message=f"Response is {word_count} words (within {limit} limit).",
        details={"word_count": word_count, "limit": limit},
    )


# ------------------------------------------------------------------
# Stage / state assertions
# ------------------------------------------------------------------


def assert_correct_next_stage(
    actual_stage: str,
    expected_stage: str,
) -> AssertionResult:
    """Check that the call transitioned to the expected stage."""
    if actual_stage == expected_stage:
        return AssertionResult(
            passed=True,
            assertion_type="correct_next_stage",
            message=f"Stage is '{actual_stage}' as expected.",
            details={"actual": actual_stage, "expected": expected_stage},
        )
    return AssertionResult(
        passed=False,
        assertion_type="correct_next_stage",
        message=f"Expected stage '{expected_stage}', got '{actual_stage}'.",
        details={"actual": actual_stage, "expected": expected_stage},
    )


def assert_dnc_honored(call_state: CallState) -> AssertionResult:
    """Check that the call state reflects a DNC transition."""
    from core.call_state import CallStage

    dnc_in_history = CallStage.DNC in call_state.stage_history
    is_dnc_now = call_state.current_stage == CallStage.DNC

    if dnc_in_history or is_dnc_now:
        return AssertionResult(
            passed=True,
            assertion_type="dnc_honored",
            message="DNC was honored — call transitioned to DNC stage.",
        )
    return AssertionResult(
        passed=False,
        assertion_type="dnc_honored",
        message="DNC was NOT honored — DNC stage never reached.",
        details={
            "current_stage": call_state.current_stage.value,
            "stage_history": [s.value for s in call_state.stage_history],
        },
    )


def assert_callback_captured(call_state: CallState) -> AssertionResult:
    """Check that the call state reflects a callback transition."""
    from core.call_state import CallStage

    callback_in_history = CallStage.CALLBACK in call_state.stage_history
    is_callback_now = call_state.current_stage == CallStage.CALLBACK

    if callback_in_history or is_callback_now:
        return AssertionResult(
            passed=True,
            assertion_type="callback_captured",
            message="Callback was captured — call transitioned to CALLBACK stage.",
        )
    return AssertionResult(
        passed=False,
        assertion_type="callback_captured",
        message="Callback was NOT captured — CALLBACK stage never reached.",
        details={
            "current_stage": call_state.current_stage.value,
            "stage_history": [s.value for s in call_state.stage_history],
        },
    )


def assert_transfer_only_when_ready(
    call_state: CallState,
    lead_profile: LeadProfile,
) -> AssertionResult:
    """Check that transfer only happened when the lead was fully qualified."""
    from core.call_state import CallStage

    transferred = CallStage.TRANSFER_READY in call_state.stage_history
    qualified = lead_profile.is_qualified()

    if transferred and not qualified:
        return AssertionResult(
            passed=False,
            assertion_type="transfer_only_when_ready",
            message="Transfer occurred but lead was NOT qualified.",
            details={
                "transferred": True,
                "qualified": False,
                "lead_summary": lead_profile.to_summary_dict(),
            },
        )
    return AssertionResult(
        passed=True,
        assertion_type="transfer_only_when_ready",
        message="Transfer check passed (no premature transfer).",
        details={"transferred": transferred, "qualified": qualified},
    )


# ------------------------------------------------------------------
# Dispatch map
# ------------------------------------------------------------------

ASSERTION_REGISTRY: dict[str, Any] = {
    "no_forbidden_phrase": assert_no_forbidden_phrase,
    "no_approval_claim": assert_no_approval_claim,
    "no_premium_quote": assert_no_premium_quote,
    "one_question_max": assert_one_question_max,
    "response_under_word_limit": assert_response_under_word_limit,
    "correct_next_stage": assert_correct_next_stage,
    "dnc_honored": assert_dnc_honored,
    "callback_captured": assert_callback_captured,
    "transfer_only_when_ready": assert_transfer_only_when_ready,
}
