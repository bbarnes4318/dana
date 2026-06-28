"""Tests for DirectResponseController live quality behaviors."""

import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.agent_runtime import AgentRuntime
from core.prompt_loader import PromptLoader
from core.state_machine import StateMachine
from core.objection_classifier import ObjectionClassifier
from core.objection_response_policy import ObjectionResponsePolicy
from rag.context_builder import ContextBuilder
from core.action_policy import ActionPolicy
from tools.tool_registry import ToolRegistry
from safety.compliance_filter import ComplianceFilter
from safety.output_validator import OutputValidator
from safety.call_stop_policy import CallStopPolicy
from safety.pii_redaction import PIIRedactor
from storage.repository import Repository
from core.intent.short_response_intent import classify_intent


@pytest.fixture
def runtime(tmp_path):
    """Return a real AgentRuntime with JSONL repository for testing."""
    loader = PromptLoader(project_root=os.path.join(os.path.dirname(__file__), ".."))
    sm = StateMachine()
    classifier = ObjectionClassifier(confidence_threshold=0.3)
    policy = ObjectionResponsePolicy()
    cb = ContextBuilder()
    action_policy = ActionPolicy()
    registry = ToolRegistry()
    comp_filter = ComplianceFilter()
    validator = OutputValidator()
    stop_policy = CallStopPolicy()
    redactor = PIIRedactor()
    repo = Repository(data_dir=tmp_path)

    # Initialize lead in state machine
    sm.lead.call_id = "test-call-123"
    sm.lead.lead_id = "test-lead-123"
    sm.lead.campaign_id = "test-campaign-123"

    return AgentRuntime(
        prompt_loader=loader,
        state_machine=sm,
        objection_classifier=classifier,
        objection_policy=policy,
        context_builder=cb,
        action_policy=action_policy,
        tool_registry=registry,
        compliance_filter=comp_filter,
        output_validator=validator,
        call_stop_policy=stop_policy,
        pii_redactor=redactor,
        repository=repo,
    )


@pytest.mark.asyncio
async def test_yes_moves_interest_check_forward(runtime: AgentRuntime):
    # Setup chat_fn to return age range prompt response
    async def chat_fn(instructions: str) -> str:
        assert "forty" in instructions or "between forty and eighty-five" in instructions.lower()
        return "Okay, first things first — are you between forty and eighty-five?"

    # On first turn (opening state), user says agreement: "yeah i agree"
    result = await runtime.process_turn("yeah i agree", chat_fn=chat_fn)
    
    assert classify_intent("yeah i agree") == "agreement"
    assert result.stage == "age_range"
    assert "forty" in result.agent_response.lower()
    assert "missed" not in result.agent_response.lower()
    assert result.should_end_call is False


@pytest.mark.asyncio
async def test_vague_where_are_you_going_is_confusion_not_missed_audio(runtime: AgentRuntime):
    # Transition runtime to interest_check stage first
    runtime.state_machine.transition("interest_check")

    async def chat_fn(instructions: str) -> str:
        return "I'm calling about the final expense options you requested. Are you open to looking at them?"

    # User says something vague: "so where are you going"
    result = await runtime.process_turn("so where are you going", chat_fn=chat_fn)
    
    assert classify_intent("so where are you going") == "confusion"
    assert "missed" not in result.agent_response.lower()
    assert "final expense" in result.agent_response.lower()
    assert result.should_end_call is False


@pytest.mark.asyncio
async def test_who_is_this_does_not_restart_full_pitch(runtime: AgentRuntime):
    runtime.state_machine.transition("interest_check")

    async def chat_fn(instructions: str) -> str:
        # Instruction suffix should prevent restarting full pitch
        return "This is Alex with American Beneficiary. I'm calling about burial options. Are you open to looking at them?"

    result = await runtime.process_turn("who is this", chat_fn=chat_fn)
    
    assert classify_intent("who is this") == "confusion"
    assert "alex" in result.agent_response.lower()
    assert "burial" in result.agent_response.lower()
    
    # Assert maximum of 4 sentences (including optional human-likeness backchannels like 'Gotcha.')
    sentences = [s for s in result.agent_response.split(".") if s.strip()]
    assert len(sentences) <= 4
    assert result.should_end_call is False


@pytest.mark.asyncio
async def test_dnc_ends_call(runtime: AgentRuntime):
    runtime.state_machine.transition("interest_check")

    # Call stop policy / direct response policy handles DNC
    result = await runtime.process_turn("do not call me again")
    
    assert classify_intent("do not call me again") == "dnc"
    assert result.stage == "dnc"
    assert "call" not in result.agent_response.lower() or "not be contacted" in result.agent_response.lower() or "won't keep you" in result.agent_response.lower()
    assert result.should_end_call is True


@pytest.mark.asyncio
async def test_wrong_number_ends_call(runtime: AgentRuntime):
    runtime.state_machine.transition("interest_check")

    result = await runtime.process_turn("wrong number")
    
    assert classify_intent("wrong number") == "wrong_number"
    assert result.stage == "end"
    assert "sorry" in result.agent_response.lower() or "wrong number" in result.agent_response.lower() or "apologize" in result.agent_response.lower() or "take care" in result.agent_response.lower()
    assert result.should_end_call is True


@pytest.mark.asyncio
async def test_no_is_refusal_not_empty_fallback(runtime: AgentRuntime):
    runtime.state_machine.transition("interest_check")

    async def chat_fn(instructions: str) -> str:
        return "Understood. I won't keep you. Take care."

    result = await runtime.process_turn("no", chat_fn=chat_fn)
    
    assert classify_intent("no") == "refusal"
    assert result.stage == "end"
    assert "missed" not in result.agent_response.lower()
    assert "greeting" not in result.agent_response.lower()
    assert result.should_end_call is True
