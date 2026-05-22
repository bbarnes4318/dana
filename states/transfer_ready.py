"""Transfer-ready handler — prospect qualifies, prepare handoff."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request, extract_yes_no
from core.lead_profile import LeadProfile
from states.base import BaseState


class TransferReadyState(BaseState):
    """Confirm the prospect is ready to speak with a licensed agent."""

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

        answer = extract_yes_no(utterance)

        if answer is True:
            return StateResult(
                next_stage=CallStage.END,
                response_guidance=(
                    "Confirm the transfer. Let them know a licensed agent "
                    "will be with them shortly. Thank them warmly."
                ),
                extracted_data={"transfer_ready": True},
                tool_calls=[{"action": "transfer_to_agent"}],
            )

        if answer is False:
            return StateResult(
                next_stage=CallStage.OBJECTION,
                response_guidance=(
                    "They're hesitant. Transition to objection handling."
                ),
            )

        # Unclear
        return StateResult(
            next_stage=None,
            response_guidance=(
                "Clarify: 'I'd love to connect you with one of our "
                "specialists who can walk you through your options. "
                "Can I go ahead and do that?'"
            ),
        )
