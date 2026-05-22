"""Tests for core.state_machine.StateMachine."""

from __future__ import annotations

import pytest

from core.call_state import CallStage, CallState
from core.lead_profile import LeadProfile
from core.state_machine import StateMachine


class TestInitialState:
    def test_initial_state_is_opening(self) -> None:
        sm = StateMachine()
        assert sm.current_stage == CallStage.OPENING


class TestQualificationOrder:
    def test_qualification_order(self) -> None:
        """Walk through the full happy-path qualification order."""
        sm = StateMachine()
        sm.lead.phone_type = "cell"  # so TEXT_CAPABLE is included

        expected_sequence = [
            CallStage.PERMISSION,
            CallStage.AGE,
            CallStage.STATE,
            CallStage.PHONE_TYPE,
            CallStage.TEXT_CAPABLE,
            CallStage.BUDGET,
            CallStage.BENEFICIARY,
            CallStage.INTEREST,
            CallStage.TRANSFER_READY,
        ]

        for expected_next in expected_sequence:
            actual = sm.get_next_stage()
            assert actual == expected_next, (
                f"Expected {expected_next} but got {actual} "
                f"(current={sm.current_stage})"
            )
            sm.call_state.transition_to(actual)


class TestTransitionToDNC:
    def test_transition_to_dnc(self) -> None:
        sm = StateMachine()
        sm.transition(CallStage.DNC.value)
        assert sm.current_stage == CallStage.DNC


class TestCanTransfer:
    def test_can_transfer_when_qualified(self) -> None:
        sm = StateMachine()
        sm.lead.age = 65
        sm.lead.state = "FL"
        sm.lead.phone_type = "cell"
        sm.lead.budget_confirmed = True
        sm.lead.transfer_ready = True

        assert sm.can_transfer() is True

    def test_cannot_transfer_when_missing_data(self) -> None:
        sm = StateMachine()
        # Only set age — missing state, phone_type, budget, etc.
        sm.lead.age = 65
        assert sm.can_transfer() is False

    def test_cannot_transfer_when_dnc(self) -> None:
        sm = StateMachine()
        sm.lead.age = 65
        sm.lead.state = "FL"
        sm.lead.phone_type = "cell"
        sm.lead.budget_confirmed = True
        sm.lead.transfer_ready = True
        sm.lead.do_not_call_requested = True

        assert sm.can_transfer() is False


class TestSkipTextCapable:
    def test_skip_text_capable_for_landline(self) -> None:
        sm = StateMachine()
        sm.lead.phone_type = "landline"
        sm.call_state.transition_to(CallStage.PHONE_TYPE)

        next_stage = sm.get_next_stage()
        assert next_stage == CallStage.BUDGET, (
            f"Expected BUDGET (skipping TEXT_CAPABLE) but got {next_stage}"
        )
