"""Scenario runner — executes eval scenarios against the state machine.

The :class:`ScenarioRunner` feeds prospect turns through the state
machine's state handlers and collects assertion results.  It does NOT
require a live LLM; agent responses are generated from the state
handler's ``response_guidance`` field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.call_state import CallStage, CallState, StateResult
from core.lead_profile import LeadProfile
from core.state_machine import StateMachine
from evals.assertions import (
    ASSERTION_REGISTRY,
    AssertionResult,
    assert_callback_captured,
    assert_correct_next_stage,
    assert_dnc_honored,
    assert_transfer_only_when_ready,
)
from evals.scenario_schema import EvalAssertion, EvalScenario
from states.age import AgeState
from states.opening import OpeningState
from states.permission import PermissionState
from states.phone_type import PhoneTypeState
from states.state_location import StateLocationState
from states.text_capable import TextCapableState

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# State handler registry — maps CallStage to handler class
# ------------------------------------------------------------------

_STATE_HANDLERS: dict[CallStage, Any] = {
    CallStage.OPENING: OpeningState(),
    CallStage.PERMISSION: PermissionState(),
    CallStage.AGE: AgeState(),
    CallStage.STATE: StateLocationState(),
    CallStage.PHONE_TYPE: PhoneTypeState(),
    CallStage.TEXT_CAPABLE: TextCapableState(),
}


@dataclass
class ScenarioResult:
    """Outcome of running a single eval scenario.

    Attributes:
        scenario_name: Name of the scenario that was run.
        passed: ``True`` if all assertions passed.
        assertion_results: Individual results for every assertion checked.
        final_stage: The call stage at the end of the scenario.
        turn_count: Total number of turns processed.
        errors: Any exceptions or errors encountered during the run.
    """

    scenario_name: str
    passed: bool
    assertion_results: list[AssertionResult] = field(default_factory=list)
    final_stage: str = ""
    turn_count: int = 0
    errors: list[str] = field(default_factory=list)


class ScenarioRunner:
    """Run an :class:`EvalScenario` through the state machine and collect results.

    The runner:
    1. Initialises a fresh :class:`StateMachine` at the scenario's
       ``initial_stage``.
    2. For each prospect turn, feeds the utterance to the current state
       handler.
    3. For agent turns, uses the ``response_guidance`` from the previous
       state-handler result as the simulated agent response.
    4. After each agent response, runs all scenario assertions.
    5. Returns a :class:`ScenarioResult` summarising pass/fail.
    """

    def run_scenario(self, scenario: EvalScenario) -> ScenarioResult:
        """Execute *scenario* and return the result.

        Parameters
        ----------
        scenario:
            The eval scenario to run.

        Returns
        -------
        ScenarioResult
            Aggregate pass/fail with per-assertion details.
        """
        # Initialise fresh state machine
        initial_stage = CallStage(scenario.initial_stage)
        call_state = CallState(
            current_stage=initial_stage,
            stage_history=[initial_stage],
        )
        lead = LeadProfile()
        sm = StateMachine(call_state=call_state, lead_profile=lead)

        all_assertion_results: list[AssertionResult] = []
        errors: list[str] = []
        turn_count = 0
        last_response_guidance = ""

        for turn in scenario.turns:
            turn_count += 1

            if turn.speaker == "prospect":
                # Feed prospect utterance through the state handler
                handler = _STATE_HANDLERS.get(sm.current_stage)
                if handler is None:
                    # No handler for this stage — record the utterance
                    # but use a passthrough result
                    last_response_guidance = (
                        f"[No handler for stage {sm.current_stage.value}] "
                        f"Prospect said: {turn.text}"
                    )
                    continue

                try:
                    result: StateResult = handler.handle(
                        utterance=turn.text,
                        lead_profile=sm.lead,
                        call_state=sm.call_state,
                    )
                    sm.apply_result(result)
                    last_response_guidance = result.response_guidance
                except Exception as exc:  # noqa: BLE001
                    err_msg = (
                        f"Error in handler for {sm.current_stage.value}: {exc}"
                    )
                    logger.exception(err_msg)
                    errors.append(err_msg)
                    continue

            elif turn.speaker == "agent":
                # Use the agent turn text as the simulated response.
                # If the scenario supplies agent text, use it; otherwise
                # fall back to the last response guidance.
                agent_response = turn.text or last_response_guidance

                # Run assertions against this agent response
                turn_assertions = self._run_assertions(
                    assertions=scenario.assertions,
                    response=agent_response,
                    call_state=sm.call_state,
                    lead_profile=sm.lead,
                    expected_final_stage=scenario.expected_final_stage,
                )
                all_assertion_results.extend(turn_assertions)

        # Final stage assertion
        final_stage_result = assert_correct_next_stage(
            actual_stage=sm.current_stage.value,
            expected_stage=scenario.expected_final_stage,
        )
        all_assertion_results.append(final_stage_result)

        overall_passed = all(r.passed for r in all_assertion_results) and not errors

        return ScenarioResult(
            scenario_name=scenario.name,
            passed=overall_passed,
            assertion_results=all_assertion_results,
            final_stage=sm.current_stage.value,
            turn_count=turn_count,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_assertions(
        assertions: list[EvalAssertion],
        response: str,
        call_state: CallState,
        lead_profile: LeadProfile,
        expected_final_stage: str,
    ) -> list[AssertionResult]:
        """Run each assertion in *assertions* and return results."""
        results: list[AssertionResult] = []

        for assertion in assertions:
            try:
                result = _dispatch_assertion(
                    assertion=assertion,
                    response=response,
                    call_state=call_state,
                    lead_profile=lead_profile,
                    expected_final_stage=expected_final_stage,
                )
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                results.append(
                    AssertionResult(
                        passed=False,
                        assertion_type=assertion.type,
                        message=f"Assertion raised an exception: {exc}",
                        details={"exception": str(exc)},
                    )
                )

        return results


def _dispatch_assertion(
    assertion: EvalAssertion,
    response: str,
    call_state: CallState,
    lead_profile: LeadProfile,
    expected_final_stage: str,
) -> AssertionResult:
    """Route an :class:`EvalAssertion` to the correct function."""

    atype = assertion.type

    if atype == "no_forbidden_phrase":
        phrases = assertion.params.get(
            "forbidden_phrases",
            assertion.expected if isinstance(assertion.expected, list) else [],
        )
        return ASSERTION_REGISTRY[atype](response, phrases)

    if atype in ("no_approval_claim", "no_premium_quote", "one_question_max"):
        return ASSERTION_REGISTRY[atype](response)

    if atype == "response_under_word_limit":
        limit = assertion.params.get("limit", assertion.expected or 50)
        return ASSERTION_REGISTRY[atype](response, limit=int(limit))

    if atype == "correct_next_stage":
        expected = assertion.expected or expected_final_stage
        return assert_correct_next_stage(
            actual_stage=call_state.current_stage.value,
            expected_stage=expected,
        )

    if atype == "dnc_honored":
        return assert_dnc_honored(call_state)

    if atype == "callback_captured":
        return assert_callback_captured(call_state)

    if atype == "transfer_only_when_ready":
        return assert_transfer_only_when_ready(call_state, lead_profile)

    # Fallback: unknown assertion type
    return AssertionResult(
        passed=False,
        assertion_type=atype,
        message=f"Unknown assertion type: {atype}",
    )
