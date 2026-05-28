"""Tests for core.action_policy.ActionPolicy.

Verifies that the policy recommends the correct tools based on
``CallState`` and profile dictionaries.
"""

from __future__ import annotations

from core.action_policy import ActionPolicy
from core.call_state import CallStage, CallState


# Shared fixtures
_EMPTY_PROFILE: dict = {}


def _make_state(stage: CallStage, **kwargs) -> CallState:
    """Create a CallState pre-set to the given stage."""
    state = CallState()
    state.transition_to(stage)
    for key, value in kwargs.items():
        setattr(state, key, value)
    return state


# ------------------------------------------------------------------
# tests
# ------------------------------------------------------------------

def test_dnc_triggers_mark_dnc() -> None:
    """When the call is in the DNC stage, mark_dnc should be recommended."""
    policy = ActionPolicy()
    state = _make_state(CallStage.DNC)

    assert policy.should_mark_dnc(state, _EMPTY_PROFILE) is True

    actions = policy.get_recommended_actions(state, _EMPTY_PROFILE)
    tool_names = [a.tool_name for a in actions]
    assert "mark_dnc" in tool_names


def test_transfer_ready_triggers_transfer() -> None:
    """TRANSFER_READY should recommend both save_lead and transfer_to_agent."""
    policy = ActionPolicy()
    state = _make_state(CallStage.TRANSFER_READY)

    assert policy.should_transfer(state, _EMPTY_PROFILE) is True
    assert policy.should_save_lead(state, _EMPTY_PROFILE) is True

    actions = policy.get_recommended_actions(state, _EMPTY_PROFILE)
    tool_names = [a.tool_name for a in actions]
    assert "save_lead" in tool_names
    assert "feTransfer" in tool_names


def test_callback_requested_triggers_callback() -> None:
    """CALLBACK stage should recommend schedule_callback."""
    policy = ActionPolicy()
    state = _make_state(CallStage.CALLBACK)

    assert policy.should_schedule_callback(state, _EMPTY_PROFILE) is True

    actions = policy.get_recommended_actions(state, _EMPTY_PROFILE)
    tool_names = [a.tool_name for a in actions]
    assert "schedule_callback" in tool_names


def test_no_actions_during_qualification() -> None:
    """Mid-qualification stages (e.g. AGE_RANGE, DECISION_MAKER) should yield no actions."""
    policy = ActionPolicy()

    for stage in (
        CallStage.OPENING,
        CallStage.INTEREST_CHECK,
        CallStage.AGE_RANGE,
        CallStage.LIVING_SITUATION,
        CallStage.DECISION_MAKER,
        CallStage.TRANSFER_CONSENT,
    ):
        state = _make_state(stage)
        actions = policy.get_recommended_actions(state, _EMPTY_PROFILE)
        assert actions == [], (
            f"Expected no actions for stage {stage.value}, got "
            f"{[a.tool_name for a in actions]}"
        )


def test_escalation_on_many_objections() -> None:
    """Three or more objections should trigger an escalation."""
    policy = ActionPolicy()
    state = _make_state(CallStage.OPENING, objection_count=3)

    assert policy.should_escalate(state, _EMPTY_PROFILE) is True

    actions = policy.get_recommended_actions(state, _EMPTY_PROFILE)
    tool_names = [a.tool_name for a in actions]
    assert "escalate_to_human" in tool_names


def test_no_escalation_below_threshold() -> None:
    """Fewer than three objections should not trigger escalation."""
    policy = ActionPolicy()
    state = _make_state(CallStage.OPENING, objection_count=2)

    assert policy.should_escalate(state, _EMPTY_PROFILE) is False
