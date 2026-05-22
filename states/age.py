"""Age state handler — collect the prospect's age."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_age
from core.lead_profile import LeadProfile
from states.base import BaseState

# Business rule: final expense products are typically for 45-85
_MIN_AGE = 45
_MAX_AGE = 85


class AgeState(BaseState):
    """Collect and validate the prospect's age."""

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

        age = extract_age(utterance)

        if age is None:
            return StateResult(
                next_stage=None,
                response_guidance=(
                    "Couldn't determine age. Politely re-ask: "
                    "'Could you tell me how old you are?'"
                ),
            )

        if age < _MIN_AGE or age > _MAX_AGE:
            return StateResult(
                next_stage=CallStage.DISQUALIFIED,
                response_guidance=(
                    f"Age {age} is outside the qualifying range "
                    f"({_MIN_AGE}-{_MAX_AGE}). Let them know kindly."
                ),
                extracted_data={
                    "age": age,
                    "disqualified_reason": f"Age {age} outside {_MIN_AGE}-{_MAX_AGE} range",
                },
            )

        return StateResult(
            next_stage=CallStage.STATE,
            response_guidance=(
                "Great, age qualifies. Now ask what state they live in."
            ),
            extracted_data={"age": age},
        )
