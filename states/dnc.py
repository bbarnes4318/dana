"""DNC handler — prospect requests to not be called again."""

from __future__ import annotations

from core.call_state import CallStage, CallState, StateResult
from core.lead_profile import LeadProfile
from states.base import BaseState


from core.canonical_responses import DNC_CLOSE

class DNCState(BaseState):
    """Process a do-not-call request."""

    def handle(
        self,
        utterance: str,
        lead_profile: LeadProfile,
        call_state: CallState,
    ) -> StateResult:
        return StateResult(
            next_stage=CallStage.END,
            response_guidance=f"Say: '{DNC_CLOSE}' then end the call.",
            extracted_data={"do_not_call_requested": True},
            tool_calls=[{"action": "add_to_dnc_list"}],
        )
