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
from states.opening import OpeningState
from states.interest_check import InterestCheckState
from states.age_range import AgeRangeState
from states.living_situation import LivingSituationState
from states.decision_maker import DecisionMakerState
from states.transfer_consent import TransferConsentState
from states.transfer_ready import TransferReadyState

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# State handler registry — maps CallStage to handler class
# ------------------------------------------------------------------

_STATE_HANDLERS: dict[CallStage, Any] = {
    CallStage.OPENING: OpeningState(),
    CallStage.INTEREST_CHECK: InterestCheckState(),
    CallStage.AGE_RANGE: AgeRangeState(),
    CallStage.LIVING_SITUATION: LivingSituationState(),
    CallStage.DECISION_MAKER: DecisionMakerState(),
    CallStage.TRANSFER_CONSENT: TransferConsentState(),
    CallStage.TRANSFER_READY: TransferReadyState(),
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

        for turn_idx, turn in enumerate(scenario.turns):
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

                # Check if this is the final agent turn
                is_final = not any(t.speaker == "agent" for t in scenario.turns[turn_idx + 1:])

                # Run assertions against this agent response
                turn_assertions = self._run_assertions(
                    assertions=scenario.assertions,
                    response=agent_response,
                    call_state=sm.call_state,
                    lead_profile=sm.lead,
                    expected_final_stage=scenario.expected_final_stage,
                    turns_history=scenario.turns[:turn_idx + 1],
                    is_final_turn=is_final,
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
        turns_history: list[EvalTurn],
        is_final_turn: bool = False,
    ) -> list[AssertionResult]:
        """Run each assertion in *assertions* and return results."""
        results: list[AssertionResult] = []

        final_assertions = {
            "correct_next_stage", "dnc_honored", "callback_captured",
            "transfer_only_when_ready", "final_stage", "final_outcome",
            "dnc_stops_call", "wrong_number_stops_call", "no_agent_turn_after_terminal",
            "profile_field_equals", "required_profile_field", "voicemail_does_not_start_agent",
            "transfer_failure_callback_offer", "disqualified_only_after_confirmation"
        }

        for assertion in assertions:
            if assertion.type in final_assertions and not is_final_turn:
                # Skip final-state assertions on intermediate turns
                continue

            try:
                result = _dispatch_assertion(
                    assertion=assertion,
                    response=response,
                    call_state=call_state,
                    lead_profile=lead_profile,
                    expected_final_stage=expected_final_stage,
                    turns_history=turns_history,
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


def get_derived_outcome(lead_profile: LeadProfile, call_state: CallState) -> str:
    if lead_profile.is_qualified():
        return "transferred"
    if lead_profile.callback_requested:
        return "callback"
    if lead_profile.do_not_call_requested or call_state.current_stage == CallStage.DNC:
        return "dnc"
    if lead_profile.disqualified_reason:
        return "disqualified"
    if call_state.current_stage == CallStage.END:
        return "ended"
    return "ended"


def _dispatch_assertion(
    assertion: EvalAssertion,
    response: str,
    call_state: CallState,
    lead_profile: LeadProfile,
    expected_final_stage: str,
    turns_history: list[EvalTurn],
) -> AssertionResult:
    """Route an :class:`EvalAssertion` to the correct function."""

    atype = assertion.type
    lower_resp = response.lower()

    if atype == "required_profile_field":
        field_name = assertion.params.get("field", assertion.expected)
        val = getattr(lead_profile, field_name, None) if hasattr(lead_profile, field_name) else lead_profile.get(field_name)
        passed = val is not None
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message=f"Profile field '{field_name}' is {val} (expected not None)." if passed else f"Profile field '{field_name}' is missing.",
        )

    if atype == "profile_field_equals":
        field_name = assertion.params.get("field")
        expected_val = assertion.expected
        val = getattr(lead_profile, field_name, None) if hasattr(lead_profile, field_name) else lead_profile.get(field_name)
        passed = (val == expected_val)
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message=f"Profile field '{field_name}' is '{val}' (expected '{expected_val}').",
        )

    if atype == "final_outcome":
        expected_outcome = assertion.expected
        actual_outcome = get_derived_outcome(lead_profile, call_state)
        passed = (actual_outcome == expected_outcome)
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message=f"Outcome is '{actual_outcome}' (expected '{expected_outcome}').",
        )

    if atype in ("final_stage", "correct_next_stage"):
        expected = assertion.expected or expected_final_stage
        passed = (call_state.current_stage.value == expected)
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message=f"Final stage is '{call_state.current_stage.value}' (expected '{expected}').",
        )

    if atype == "no_sensitive_info_request":
        sensitive_terms = ["social security", "ssn", "date of birth", "dob", "bank account", "credit card", "routing number", "medicare", "payment details", "payment info"]
        found = [t for t in sensitive_terms if t in lower_resp]
        passed = len(found) == 0
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="No sensitive info requested." if passed else f"Agent requested sensitive info: {found}",
        )

    if atype == "no_human_claim":
        forbidden_human_claims = ["i'm a real person", "i am a real person", "i'm human", "i am human", "i'm a human", "yes, i am a real person", "yes i am a real person"]
        found = [p for p in forbidden_human_claims if p in lower_resp]
        passed = len(found) == 0
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="No human claims detected." if passed else f"Agent claimed to be human/person: {found}",
        )

    if atype == "no_licensed_claim":
        from qa.scoring import is_licensed_claim
        passed = not is_licensed_claim(response)
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="No licensed agent claims detected." if passed else "Agent claimed to be licensed.",
        )

    if atype == "required_identity_response":
        has_intro = "this is alex" in lower_resp and "american beneficiary" in lower_resp
        has_check = "checking if you" in lower_resp or "still open" in lower_resp or "final expense" in lower_resp
        passed = has_intro and has_check
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="Agent gave correct identity response." if passed else f"Agent failed identity response requirements. Got: '{response}'",
        )

    if atype in ("no_transfer_before_ready", "transfer_only_when_ready"):
        transferred = call_state.current_stage == CallStage.TRANSFER_READY or "transfer_ready" in [s.value for s in call_state.stage_history]
        qualified = lead_profile.is_qualified()
        passed = not (transferred and not qualified)
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="Transfer readiness check passed." if passed else "Transfer occurred prematurely or when lead was not qualified.",
        )

    if atype == "transfer_failure_callback_offer":
        passed = "call you back" in lower_resp or "later today" in lower_resp or "tomorrow" in lower_resp
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="Callback offered on transfer failure." if passed else "Callback not offered on transfer failure.",
        )

    if atype == "disqualified_only_after_confirmation":
        is_disqualified = call_state.current_stage == CallStage.DISQUALIFIED
        passed = True
        if is_disqualified:
            agent_text = " ".join([t.text.lower() for t in turns_history if t.speaker == "agent"])
            passed = "heard you right" in agent_text or "make sure" in agent_text
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="Disqualification confirmed before ending." if passed else "Call disqualified without confirming answer first.",
        )

    if atype in ("dnc_stops_call", "dnc_honored"):
        is_dnc = call_state.current_stage in (CallStage.DNC, CallStage.END)
        passed = is_dnc
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="DNC request honored and stopped call." if passed else "DNC request not honored.",
        )

    if atype == "wrong_number_stops_call":
        is_end = call_state.current_stage == CallStage.END
        passed = is_end
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="Wrong number request stopped the call." if passed else "Wrong number did not stop the call.",
        )

    if atype == "no_agent_turn_after_terminal":
        passed = call_state.current_stage in (CallStage.END, CallStage.DNC, CallStage.DISQUALIFIED, CallStage.CALLBACK)
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="No agent turn after terminal stage.",
        )

    if atype == "no_markdown":
        from qa.scoring import _MARKDOWN_PATTERN
        passed = not bool(_MARKDOWN_PATTERN.search(response))
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="No markdown detected." if passed else "Response contains markdown.",
        )

    if atype in ("max_one_question", "one_question_max"):
        count = response.count("?")
        passed = count <= 1
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message=f"At most one question asked ({count})." if passed else f"Too many questions asked ({count}).",
        )

    if atype in ("max_agent_words", "response_under_word_limit"):
        limit = assertion.params.get("limit", assertion.expected or 60)
        word_count = len(response.split())
        passed = word_count <= int(limit)
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message=f"Agent response is {word_count} words (limit {limit}).",
        )

    if atype == "required_phrase":
        phrases = assertion.params.get("phrases", assertion.expected)
        if isinstance(phrases, str):
            phrases = [phrases]
        found = [p for p in phrases if p.lower() in lower_resp]
        passed = len(found) == len(phrases)
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message=f"All required phrases found." if passed else f"Missing required phrases. Expected {phrases}, got {found}",
        )

    if atype in ("forbidden_phrase", "no_forbidden_phrase"):
        phrases = assertion.params.get(
            "forbidden_phrases",
            assertion.expected if isinstance(assertion.expected, list) else [],
        )
        if isinstance(phrases, str):
            phrases = [phrases]
        found = [p for p in phrases if p.lower() in lower_resp]
        passed = len(found) == 0
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="No forbidden phrases found." if passed else f"Forbidden phrases found: {found}",
        )

    if atype == "callback_captured":
        passed = lead_profile.callback_requested is True or call_state.current_stage == CallStage.CALLBACK
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="Callback captured successfully." if passed else "Callback not captured.",
        )

    if atype == "voicemail_does_not_start_agent":
        passed = call_state.current_stage == CallStage.END
        return AssertionResult(
            passed=passed,
            assertion_type=atype,
            message="Voicemail did not start agent conversation." if passed else "Voicemail started agent conversation incorrectly.",
        )

    # Fallback: route to legacy map if present
    if atype in ASSERTION_REGISTRY:
        if atype == "no_forbidden_phrase":
            phrases = assertion.params.get("forbidden_phrases", assertion.expected if isinstance(assertion.expected, list) else [])
            return ASSERTION_REGISTRY[atype](response, phrases)
        if atype == "response_under_word_limit":
            limit = assertion.params.get("limit", assertion.expected or 50)
            return ASSERTION_REGISTRY[atype](response, limit=int(limit))
        if atype == "correct_next_stage":
            expected = assertion.expected or expected_final_stage
            return ASSERTION_REGISTRY[atype](call_state.current_stage.value, expected)
        return ASSERTION_REGISTRY[atype](response)

    return AssertionResult(
        passed=False,
        assertion_type=atype,
        message=f"Unknown assertion type: {atype}",
    )
