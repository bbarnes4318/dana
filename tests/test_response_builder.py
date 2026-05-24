"""Unit tests for ResponseBuilder."""

from __future__ import annotations

import pytest

from core.call_state import CallStage, CallState, StateResult
from core.lead_profile import LeadProfile
from core.objection_response_policy import ObjectionGuidance
from core.response_builder import ResponseBuilder


def test_build_instructions_basic() -> None:
    rb = ResponseBuilder()
    call_state = CallState(current_stage=CallStage.OPENING, turn_count=1)
    lead = LeadProfile(first_name="John")
    handler_result = StateResult(response_guidance="Greet the prospect.")

    inst = rb.build_instructions(
        call_state=call_state,
        lead_profile=lead,
        objection_guidance=None,
        rag_context=None,
        stage_handler_result=handler_result,
    )

    assert "CURRENT CONVERSATION STATE" in inst
    assert "OPENING" in inst
    assert "Greet the prospect." in inst
    assert "Name: John" in inst


def test_build_instructions_with_objection_and_rag() -> None:
    rb = ResponseBuilder()
    call_state = CallState(
        current_stage=CallStage.BUDGET, turn_count=3, objection_count=1
    )
    lead = LeadProfile(first_name="Jane", age=65)
    handler_result = StateResult(response_guidance="Ask about budget.")
    obg = ObjectionGuidance(
        intent="no_money",
        guidance_text="Explain affordability.",
        max_attempts=1,
        should_end_call=False,
        next_stage="qualifying",
    )
    rag = "Some policy rules"

    inst = rb.build_instructions(
        call_state=call_state,
        lead_profile=lead,
        objection_guidance=obg,
        rag_context=rag,
        stage_handler_result=handler_result,
    )

    assert "An objection of type 'no_money' was detected." in inst
    assert "Explain affordability." in inst
    assert "KNOWLEDGE CONTEXT" in inst
    assert "Some policy rules" in inst
