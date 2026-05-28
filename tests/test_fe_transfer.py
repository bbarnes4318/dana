"""Unit tests for the feTransfer workflow and integration with AgentRuntime."""

import os
import pytest
from pathlib import Path

from core.call_state import CallStage
from core.agent_runtime import AgentRuntime
from core.state_machine import StateMachine
from core.objection_classifier import ObjectionClassifier
from core.objection_response_policy import ObjectionResponsePolicy
from core.prompt_loader import PromptLoader
from core.action_policy import ActionPolicy
from tools.tool_registry import ToolRegistry
from safety.compliance_filter import ComplianceFilter
from safety.output_validator import OutputValidator
from safety.call_stop_policy import CallStopPolicy
from safety.pii_redaction import PIIRedactor
from storage.repository import Repository
from rag.context_builder import ContextBuilder
from telephony.fe_transfer import fe_transfer, FeTransferResult


@pytest.fixture
def runtime(tmp_path: Path) -> AgentRuntime:
    """Create an AgentRuntime instance for testing."""
    project_root = Path(__file__).resolve().parent.parent
    loader = PromptLoader(project_root=project_root)
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
async def test_fe_transfer_unconfigured_phone(monkeypatch):
    # Ensure agent phone is unconfigured or replace_me
    monkeypatch.delenv("LICENSED_AGENT_PHONE_NUMBER", raising=False)
    
    res = await fe_transfer(
        room_name="test_room",
        prospect_identity="John Doe",
        licensed_agent_phone_number=None,
        call_summary="Qualified lead summary",
        transfer_reason="Lead qualified"
    )
    
    assert res.success is False
    assert res.reason == "licensed_agent_phone_number_not_configured"


@pytest.mark.asyncio
async def test_fe_transfer_not_confirmed(monkeypatch):
    monkeypatch.setenv("LICENSED_AGENT_PHONE_NUMBER", "+15551234567")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "no")
    
    res = await fe_transfer(
        room_name="test_room",
        prospect_identity="John Doe",
        licensed_agent_phone_number=None,
        call_summary="Qualified lead summary",
        transfer_reason="Lead qualified"
    )
    
    assert res.success is False
    assert res.reason == "transfer_not_confirmed"


@pytest.mark.asyncio
async def test_fe_transfer_not_implemented(monkeypatch):
    monkeypatch.setenv("LICENSED_AGENT_PHONE_NUMBER", "+15551234567")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "yes")
    
    res = await fe_transfer(
        room_name="test_room",
        prospect_identity="John Doe",
        licensed_agent_phone_number=None,
        call_summary="Qualified lead summary",
        transfer_reason="Lead qualified"
    )
    
    assert res.success is False
    assert res.reason == "fe_transfer_not_implemented"


@pytest.mark.asyncio
async def test_runtime_transfer_failure_transitions_to_callback(runtime: AgentRuntime, monkeypatch) -> None:
    # Set config values
    monkeypatch.setenv("LICENSED_AGENT_PHONE_NUMBER", "+15551234567")
    monkeypatch.setenv("DANA_CONFIRM_TRANSFER_CALL", "no")  # Trigger failure path

    # Force transition the state machine to TRANSFER_READY
    runtime.state_machine.call_state.transition_to(CallStage.TRANSFER_READY)
    assert runtime.state_machine.call_state.current_stage == CallStage.TRANSFER_READY

    # Qualify the lead profile
    runtime.state_machine.lead.open_to_review = True
    runtime.state_machine.lead.age_range_confirmed = True
    runtime.state_machine.lead.living_independently = True
    runtime.state_machine.lead.financial_decision_maker = True
    runtime.state_machine.lead.transfer_consent_confirmed = True

    # Process turn
    result = await runtime.process_turn("I am ready to speak with an agent.")

    # 1. State machine should transition to CALLBACK stage
    assert runtime.state_machine.call_state.current_stage == CallStage.CALLBACK
    assert result.stage == "callback"

    # 2. Agent response should be overridden to offer a callback
    assert "unable to connect" in result.agent_response
    assert "schedule a convenient time" in result.agent_response

