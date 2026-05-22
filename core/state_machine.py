"""Core state machine driving the Dana qualification call flow.

The :class:`StateMachine` owns a :class:`CallState` and a
:class:`LeadProfile` and is responsible for orchestrating transitions
between call stages based on events produced by individual state handlers.
"""

from __future__ import annotations

from typing import Any, Optional

from core.call_state import CallStage, CallState, StateResult
from core.lead_profile import LeadProfile


# Qualification order — the "happy path" through the call.
_QUALIFICATION_ORDER: list[CallStage] = [
    CallStage.OPENING,
    CallStage.PERMISSION,
    CallStage.AGE,
    CallStage.STATE,
    CallStage.PHONE_TYPE,
    CallStage.TEXT_CAPABLE,  # only visited if phone_type == "cell"
    CallStage.BUDGET,
    CallStage.BENEFICIARY,
    CallStage.INTEREST,
    CallStage.TRANSFER_READY,
]


class StateMachine:
    """Drives stage-to-stage transitions for a single call.

    Parameters:
        call_state: Pre-existing :class:`CallState` (defaults to a fresh one).
        lead_profile: Pre-existing :class:`LeadProfile` (defaults to a fresh one).
    """

    def __init__(
        self,
        call_state: CallState | None = None,
        lead_profile: LeadProfile | None = None,
    ) -> None:
        self.call_state = call_state or CallState()
        self.lead = lead_profile or LeadProfile()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transition(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Apply an *event* (typically a :pyattr:`CallStage` value string)
        and optional *data* to the state machine.

        This is the primary entry-point used by the orchestrator after a
        state handler returns a :class:`StateResult`.
        """
        data = data or {}

        # Apply extracted data to the lead profile
        for key, value in data.items():
            if hasattr(self.lead, key) and value is not None:
                setattr(self.lead, key, value)

        # Determine target stage
        try:
            target = CallStage(event)
        except ValueError:
            # Unknown event — stay put
            return

        self.call_state.transition_to(target)

    def get_next_stage(self) -> CallStage:
        """Return the next stage in the qualification order.

        Skips ``TEXT_CAPABLE`` when the phone type is not ``"cell"``.
        If the current stage is not in the qualification order (e.g.
        OBJECTION, DNC), returns the current stage unchanged.
        """
        current = self.call_state.current_stage

        try:
            idx = _QUALIFICATION_ORDER.index(current)
        except ValueError:
            return current

        next_idx = idx + 1
        if next_idx >= len(_QUALIFICATION_ORDER):
            return CallStage.TRANSFER_READY

        candidate = _QUALIFICATION_ORDER[next_idx]

        # Skip TEXT_CAPABLE for landline users
        if candidate == CallStage.TEXT_CAPABLE and self.lead.phone_type != "cell":
            next_idx += 1
            if next_idx >= len(_QUALIFICATION_ORDER):
                return CallStage.TRANSFER_READY
            candidate = _QUALIFICATION_ORDER[next_idx]

        return candidate

    def can_transfer(self) -> bool:
        """Return ``True`` if the lead currently meets transfer criteria.

        Transfer ready ONLY if:
        - age present
        - state present
        - phone_type known
        - budget confirmed **or** strong interest (``"high"``)
        - no DNC
        - no disqualifier
        - prospect willing (``transfer_ready`` flag)
        """
        return self.lead.is_qualified()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def current_stage(self) -> CallStage:
        return self.call_state.current_stage

    def apply_result(self, result: StateResult) -> None:
        """Convenience: apply a :class:`StateResult` from a state handler.

        Updates the lead profile with extracted data and transitions to
        the next stage if one was specified.
        """
        # Merge extracted data
        for key, value in result.extracted_data.items():
            if hasattr(self.lead, key) and value is not None:
                setattr(self.lead, key, value)

        # Transition
        if result.next_stage is not None:
            self.call_state.transition_to(result.next_stage)
