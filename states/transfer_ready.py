"""Transfer-ready handler — prospect has consented and is being connected to an agent."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.extraction import detect_callback_request, detect_dnc_request
from core.lead_profile import LeadProfile
from states.base import BaseState


class TransferReadyState(BaseState):
    """Final stage handler to manage any final utterance during connection."""

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

        # If they speak while we are connecting, reassure them and keep in TRANSFER_READY or transition to END
        return StateResult(
            next_stage=CallStage.END,
            response_guidance="Acknowledge politely: 'Hold on one moment, connecting you now.'",
            extracted_data={"transfer_ready": True}
        )
