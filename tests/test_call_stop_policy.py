"""Tests for safety.call_stop_policy.CallStopPolicy."""

from __future__ import annotations

import pytest

from core.call_state import CallState
from safety.call_stop_policy import CallStopPolicy, StopDecision


@pytest.fixture
def policy() -> CallStopPolicy:
    return CallStopPolicy()


@pytest.fixture
def call_state() -> CallState:
    return CallState()


# --------------------------------------------------------------------- #
# DNC phrases
# --------------------------------------------------------------------- #


def test_dnc_phrase_stops(policy: CallStopPolicy, call_state: CallState) -> None:
    """Do-Not-Call phrases should immediately stop the call."""
    for phrase in [
        "Stop calling me",
        "stop calling",
        "do not call me again",
        "don't call me",
        "Take me off your list",
        "take me off the list",
    ]:
        # Fresh policy per phrase to reset refusal counter
        p = CallStopPolicy()
        decision = p.should_stop(phrase, call_state)
        assert decision.should_stop is True, f"Expected stop for: {phrase}"
        assert decision.stop_type == "dnc"


# --------------------------------------------------------------------- #
# Remove-me phrases
# --------------------------------------------------------------------- #


def test_remove_me_stops(policy: CallStopPolicy, call_state: CallState) -> None:
    """'Remove me' / 'remove my number' should stop the call as DNC."""
    for phrase in ["Remove me from your list", "remove my number please"]:
        p = CallStopPolicy()
        decision = p.should_stop(phrase, call_state)
        assert decision.should_stop is True, f"Expected stop for: {phrase}"
        assert decision.stop_type == "dnc"


# --------------------------------------------------------------------- #
# Wrong number
# --------------------------------------------------------------------- #


def test_wrong_number_stops(
    policy: CallStopPolicy, call_state: CallState
) -> None:
    """'Wrong number' should stop the call."""
    decision = policy.should_stop("You have the wrong number", call_state)
    assert decision.should_stop is True
    assert decision.stop_type == "wrong_number"


# --------------------------------------------------------------------- #
# Normal speech continues
# --------------------------------------------------------------------- #


def test_normal_speech_continues(
    policy: CallStopPolicy, call_state: CallState
) -> None:
    """Normal conversational responses should not trigger a stop."""
    for phrase in [
        "I'm doing well, thanks.",
        "Tell me more about the coverage.",
        "What does the plan include?",
        "I need to think about it.",
    ]:
        decision = policy.should_stop(phrase, call_state)
        assert decision.should_stop is False, f"Unexpected stop for: {phrase}"


# --------------------------------------------------------------------- #
# Repeated refusal
# --------------------------------------------------------------------- #


def test_repeated_refusal_stops(call_state: CallState) -> None:
    """Three consecutive 'no' responses should trigger a stop."""
    policy = CallStopPolicy()

    # First two should not stop
    decision1 = policy.should_stop("No", call_state)
    assert decision1.should_stop is False

    decision2 = policy.should_stop("no", call_state)
    assert decision2.should_stop is False

    # Third consecutive refusal should stop
    decision3 = policy.should_stop("No.", call_state)
    assert decision3.should_stop is True
    assert decision3.stop_type == "repeated_refusal"


def test_refusal_resets_on_non_refusal(call_state: CallState) -> None:
    """A non-refusal response in between should reset the counter."""
    policy = CallStopPolicy()

    policy.should_stop("No", call_state)
    policy.should_stop("No", call_state)
    # Non-refusal resets the counter
    policy.should_stop("Tell me more", call_state)
    decision = policy.should_stop("No", call_state)
    assert decision.should_stop is False
