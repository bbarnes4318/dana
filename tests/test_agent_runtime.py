"""Unit and integration tests for the AgentRuntime orchestrator."""

from __future__ import annotations

import pytest
from pathlib import Path

from core.call_state import CallStage, CallState
from core.state_machine import StateMachine
from core.objection_classifier import ObjectionClassifier
from core.objection_response_policy import ObjectionResponsePolicy
from core.prompt_loader import PromptLoader
from core.response_builder import ResponseBuilder
from rag.context_builder import ContextBuilder
from core.action_policy import ActionPolicy
from tools.tool_registry import ToolRegistry
from safety.compliance_filter import ComplianceFilter
from safety.output_validator import OutputValidator
from safety.call_stop_policy import CallStopPolicy
from safety.pii_redaction import PIIRedactor
from storage.repository import Repository
from core.agent_runtime import AgentRuntime


@pytest.fixture
def runtime(tmp_path: Path) -> AgentRuntime:
    """Create an AgentRuntime instance with all dependencies (using a temporary JSONL store)."""
    project_root = Path(__file__).resolve().parent.parent
    
    loader = PromptLoader(project_root=project_root)
    sm = StateMachine()
    classifier = ObjectionClassifier(confidence_threshold=0.3)
    policy = ObjectionResponsePolicy()
    
    # Context builder requires a retriever, vector store, etc.
    cb = ContextBuilder()
    
    action_policy = ActionPolicy()
    registry = ToolRegistry()
    comp_filter = ComplianceFilter()
    validator = OutputValidator()
    stop_policy = CallStopPolicy()
    redactor = PIIRedactor()
    
    # Repository configured to use a temporary directory for JSONL output
    repo = Repository(data_dir=tmp_path)
    
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
async def test_runtime_happy_path(runtime: AgentRuntime) -> None:
    # 1. Opening turn
    result = await runtime.process_turn("Hello?")
    assert result.stage == "interest_check"
    assert "open to looking" in result.agent_response.lower() or "interest" in result.agent_response.lower()
    assert result.should_end_call is False

    # 2. Interest Check turn - say yes
    result2 = await runtime.process_turn("Yeah, sure, I am interested.")
    assert result2.stage == "age_range"
    assert "forty" in result2.agent_response or "eighty-five" in result2.agent_response
    assert result2.should_end_call is False


@pytest.mark.asyncio
async def test_runtime_objection_handled(runtime: AgentRuntime) -> None:
    # Go to interest check stage
    runtime.state_machine.call_state.transition_to(CallStage.INTEREST_CHECK)
    
    # Prospect raises an objection about money
    result = await runtime.process_turn("I'm interested but I don't have the money.")
    # Should detect objection and increase objection count
    assert runtime.state_machine.call_state.objection_count == 1
    # Check that objection guidance was processed (policy says transition to next qualification stage which is AGE_RANGE)
    assert result.stage == "age_range"


@pytest.mark.asyncio
async def test_runtime_dnc_stop(runtime: AgentRuntime) -> None:
    # DNC request should trigger immediate stop
    result = await runtime.process_turn("Do not call me again.")
    assert result.stage == "dnc"
    assert result.should_end_call is True
    assert any("removed" in res.lower() or "dnc" in res.lower() for res in result.tool_results)


@pytest.mark.asyncio
async def test_runtime_underage_disqualification(runtime: AgentRuntime) -> None:
    # Go to AGE_RANGE stage
    runtime.state_machine.call_state.transition_to(CallStage.AGE_RANGE)
    
    # User is 25 (under limit of 40)
    result = await runtime.process_turn("I am 25 years old.")
    # Should transition to confirmation gate first
    assert result.stage == "age_range"
    assert "confirming_age" in runtime.state_machine.lead.notes
    assert result.should_end_call is False

    # Confirm the disqualification
    result2 = await runtime.process_turn("Yes, correct.")
    assert result2.stage == "disqualified"
    assert result2.should_end_call is True
    assert runtime.state_machine.lead.disqualified_reason is not None

