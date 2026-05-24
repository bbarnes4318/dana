"""ActionPolicy — decide which tools should fire based on call state.

The policy inspects the current :class:`CallState` and an optional lead
profile dict to produce a list of recommended tool actions.  It is
intentionally deterministic and rule-based so behaviour is predictable
and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.call_state import CallStage, CallState


@dataclass
class RecommendedAction:
    """A single recommended tool invocation.

    Attributes:
        tool_name: Name of the tool to invoke (must match registry).
        reason: Human-readable explanation of why this action is
            recommended.
        params: Suggested parameters to pass to the tool.
    """

    tool_name: str
    reason: str
    params: dict[str, Any] = field(default_factory=dict)


class ActionPolicy:
    """Determine which tools should fire given the current call state.

    Each ``should_*`` method is a pure predicate — it inspects state and
    profile and returns ``True`` if the corresponding action is warranted.
    :meth:`get_recommended_actions` aggregates all predicates into a list
    of :class:`RecommendedAction` objects.
    """

    # ------------------------------------------------------------------
    # predicates
    # ------------------------------------------------------------------

    @staticmethod
    def should_save_lead(state: CallState, profile: dict[str, Any]) -> bool:
        """Save the lead when we reach the transfer-ready stage.

        A lead is worth saving when we have collected enough qualification
        data *and* the call has progressed to the transfer-ready stage.
        """
        return state.current_stage == CallStage.TRANSFER_READY

    @staticmethod
    def should_transfer(state: CallState, profile: dict[str, Any]) -> bool:
        """Transfer when the lead is fully qualified and ready."""
        return state.current_stage == CallStage.TRANSFER_READY

    @staticmethod
    def should_schedule_callback(
        state: CallState, profile: dict[str, Any]
    ) -> bool:
        """Schedule a callback when the lead explicitly requests one."""
        return state.current_stage == CallStage.CALLBACK

    @staticmethod
    def should_mark_dnc(state: CallState, profile: dict[str, Any]) -> bool:
        """Mark DNC when the lead asks to be placed on the list."""
        return state.current_stage == CallStage.DNC

    @staticmethod
    def should_escalate(state: CallState, profile: dict[str, Any]) -> bool:
        """Escalate when too many objections pile up or on explicit request.

        Heuristic: three or more objections in a single call signals that
        a human should take over.
        """
        return state.objection_count >= 3

    # ------------------------------------------------------------------
    # aggregation
    # ------------------------------------------------------------------

    def get_recommended_actions(
        self,
        state: CallState,
        profile: dict[str, Any],
    ) -> list[RecommendedAction]:
        """Return a list of actions that should fire right now.

        The list may be empty (e.g. during normal qualification flow).
        """
        actions: list[RecommendedAction] = []

        if self.should_mark_dnc(state, profile):
            actions.append(
                RecommendedAction(
                    tool_name="mark_dnc",
                    reason="Lead requested Do-Not-Call",
                )
            )

        if self.should_save_lead(state, profile):
            actions.append(
                RecommendedAction(
                    tool_name="save_lead",
                    reason="Lead is fully qualified",
                )
            )

        if self.should_transfer(state, profile):
            actions.append(
                RecommendedAction(
                    tool_name="feTransfer",
                    reason="Lead qualified and ready for agent transfer",
                )
            )

        if self.should_schedule_callback(state, profile):
            actions.append(
                RecommendedAction(
                    tool_name="schedule_callback",
                    reason="Lead requested a callback",
                )
            )

        if self.should_escalate(state, profile):
            actions.append(
                RecommendedAction(
                    tool_name="escalate_to_human",
                    reason="Multiple objections detected — escalating",
                )
            )

        return actions
