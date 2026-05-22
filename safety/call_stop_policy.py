"""Call-stop policy for Dana voice agent.

Detects utterances that legally or ethically require the agent to
immediately stop the call, including Do-Not-Call requests,
wrong-number indicators, and repeated refusals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.call_state import CallState


@dataclass
class StopDecision:
    """Decision on whether the call should be terminated.

    Attributes:
        should_stop: ``True`` if the call must end immediately.
        reason: Human-readable explanation of why.
        stop_type: Category code — one of ``'dnc'``, ``'angry'``,
            ``'wrong_number'``, or ``'repeated_refusal'``.
    """

    should_stop: bool = False
    reason: str = ""
    stop_type: str = ""


# DNC / removal phrases — these trigger an immediate stop.
_DNC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bstop\s+calling(?:\s+me)?\b", re.IGNORECASE),
    re.compile(r"\bremove\s+(?:me|my\s+number)\b", re.IGNORECASE),
    re.compile(r"\bdo\s*n[o']?t\s+call\b", re.IGNORECASE),
    re.compile(r"\btake\s+me\s+off\s+(?:your|the)\s+list\b", re.IGNORECASE),
]

# Wrong-number phrase.
_WRONG_NUMBER_PATTERN = re.compile(r"\bwrong\s+number\b", re.IGNORECASE)

# Threshold for consecutive refusals.
_REPEATED_REFUSAL_THRESHOLD = 3

# Simple refusal patterns.
_REFUSAL_PATTERN = re.compile(
    r"^\s*(?:no+|nah|nope|not?\s+interested|no\s+thanks?|no\s+thank\s+you)\s*[.!]?\s*$",
    re.IGNORECASE,
)


class CallStopPolicy:
    """Decides whether a call must be stopped based on the prospect's utterance.

    Usage::

        policy = CallStopPolicy()
        decision = policy.should_stop("Take me off your list", call_state)
        if decision.should_stop:
            # hang up and mark DNC
            ...
    """

    def __init__(self) -> None:
        self._consecutive_refusals: int = 0

    def should_stop(
        self, utterance: str, call_state: CallState
    ) -> StopDecision:
        """Evaluate *utterance* and decide whether to stop the call.

        Args:
            utterance: The prospect's latest spoken text.
            call_state: Current call state (used for context).

        Returns:
            A :class:`StopDecision`.
        """
        # --- DNC phrases (immediate stop) ---
        for pattern in _DNC_PATTERNS:
            if pattern.search(utterance):
                return StopDecision(
                    should_stop=True,
                    reason="Prospect requested Do-Not-Call",
                    stop_type="dnc",
                )

        # --- wrong number ---
        if _WRONG_NUMBER_PATTERN.search(utterance):
            return StopDecision(
                should_stop=True,
                reason="Prospect indicated wrong number",
                stop_type="wrong_number",
            )

        # --- repeated refusal tracking ---
        if _REFUSAL_PATTERN.match(utterance):
            self._consecutive_refusals += 1
        else:
            self._consecutive_refusals = 0

        if self._consecutive_refusals >= _REPEATED_REFUSAL_THRESHOLD:
            return StopDecision(
                should_stop=True,
                reason=(
                    f"Prospect refused {self._consecutive_refusals} "
                    "times consecutively"
                ),
                stop_type="repeated_refusal",
            )

        # --- all clear ---
        return StopDecision(should_stop=False)
