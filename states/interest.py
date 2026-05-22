"""Interest handler — gauge the prospect's interest level."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no
from core.lead_profile import LeadProfile
from states.base import BaseState

_INTEREST_KEYWORDS = {
    "high": [
        "very interested", "absolutely", "definitely", "sounds great",
        "sign me up", "let's do it", "i want", "love to",
    ],
    "medium": [
        "maybe", "possibly", "could be", "tell me more", "interested",
        "i guess", "might",
    ],
    "low": [
        "not sure", "probably not", "don't know", "i'll think about it",
    ],
    "none": [
        "not interested", "no way", "absolutely not", "never",
    ],
}


class InterestState(BaseState):
    """Gauge the prospect's interest in moving forward."""

    def handle(
        self,
        utterance: str,
        lead_profile: LeadProfile,
        call_state: CallState,
    ) -> StateResult:
        if detect_dnc_request(utterance):
            return StateResult(
                next_stage=CallStage.DNC,
                response_guidance="Acknowledge and process the do-not-call request.",
            )

        if detect_callback_request(utterance):
            return StateResult(
                next_stage=CallStage.CALLBACK,
                response_guidance="Acknowledge the callback request politely.",
            )

        level = self._classify(utterance)

        if level is None:
            return StateResult(
                next_stage=None,
                response_guidance=(
                    "Gauge interest: 'On a scale from very interested to "
                    "not interested, how would you feel about learning "
                    "more from one of our specialists today?'"
                ),
            )

        extracted = {"interest_level": level}

        if level in ("high", "medium"):
            return StateResult(
                next_stage=CallStage.TRANSFER_READY,
                response_guidance=(
                    "They're interested! Prepare to transfer them to a "
                    "licensed agent. Confirm they're ready."
                ),
                extracted_data=extracted,
            )

        if level == "low":
            return StateResult(
                next_stage=CallStage.OBJECTION,
                response_guidance=(
                    "Interest is lukewarm. Try a soft rebuttal to see "
                    "if you can raise their interest before ending."
                ),
                extracted_data=extracted,
            )

        # "none"
        return StateResult(
            next_stage=CallStage.END,
            response_guidance=(
                "They're not interested. Thank them for their time and "
                "end the call respectfully."
            ),
            extracted_data=extracted,
        )

    @staticmethod
    def _classify(text: str) -> str | None:
        lower = text.lower()
        for level, keywords in _INTEREST_KEYWORDS.items():
            for kw in keywords:
                if kw in lower:
                    return level

        # Fall back to yes/no
        yn = extract_yes_no(text)
        if yn is True:
            return "high"
        if yn is False:
            return "none"

        return None
