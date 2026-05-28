"""Unit and integration tests for the human-likeness layer."""

from __future__ import annotations

import pytest
from pathlib import Path
from datetime import datetime, timezone

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

from voice.backchannel_policy import BackchannelPolicy, check_confusion_or_hostility
from voice.dialogue_style import DialogueStyleController
from voice.repetition_guard import RepetitionGuard
from voice.prosody_controller import ProsodyController
from voice.spoken_output_auditor import SpokenOutputAuditor


def test_backchannel_policy() -> None:
    policy = BackchannelPolicy()
    
    # 1. Opening and answered stages should not have backchannels
    assert policy.select_backchannel("opening", "yes, I am interested in burial expenses.", 1, False) is None
    assert policy.select_backchannel("answered", "yes, hello?", 2, False) is None
    assert policy.select_backchannel("dnc", "remove me", 3, False) is None
    assert policy.select_backchannel("disqualified", "no", 4, False) is None
    assert policy.select_backchannel("end", "bye", 5, False) is None
    
    # Reset policy state
    policy = BackchannelPolicy()
    
    # 2. Substantive check (<= 2 words should not trigger backchannel)
    assert policy.select_backchannel("age_range", "hello there", 1, False) is None
    
    # 3. Two turns in a row check & rotation
    # Turn 1: substantive, valid stage -> Should get a backchannel
    bc1 = policy.select_backchannel("age_range", "yes, I am forty-five years old.", 1, False)
    assert bc1 in ["Okay.", "Gotcha.", "Fair enough.", "That makes sense.", "Understood.", "Right."]
    
    # Turn 2: substantive, but last turn had a backchannel, so it should be None
    bc2 = policy.select_backchannel("living_situation", "I live in my own home.", 2, False)
    assert bc2 is None
    
    # Turn 3: substantive, no backchannel on last turn, so should select
    bc3 = policy.select_backchannel("decision_maker", "I make the financial decisions.", 3, False)
    assert bc3 is not None
    assert bc3 != bc1  # Should rotate to a different backchannel
    
    # 4. Silence check
    policy = BackchannelPolicy()
    assert policy.select_backchannel("age_range", "silence", 1, False) is None
    assert policy.select_backchannel("age_range", "", 1, False) is None


def test_check_confusion_or_hostility() -> None:
    is_confused, is_hostile = check_confusion_or_hostility("what? repeat that please.")
    assert is_confused is True
    assert is_hostile is False
    
    is_confused, is_hostile = check_confusion_or_hostility("stop calling me, fucking scam!")
    assert is_confused is False
    assert is_hostile is True


def test_clean_perfect_usage() -> None:
    policy = BackchannelPolicy()
    
    # Use "Perfect" after transfer consent (TRANSFER_READY) -> Should keep
    assert policy.clean_perfect_usage("Perfect. Hold the line.", "transfer_ready", "yes, connect me", False) == "Perfect. Hold the line."
    
    # Use "Perfect" after callback stage -> Should keep
    assert policy.clean_perfect_usage("Perfect. I will call you back.", "callback", "tomorrow at 3", False) == "Perfect. I will call you back."
    
    # Under age_range (not transfer_ready/callback) -> Should strip
    assert policy.clean_perfect_usage("Perfect. Are you between forty and eighty-five?", "age_range", "yes", False) == "Are you between forty and eighty-five?"
    
    # After confusion -> Should strip
    assert policy.clean_perfect_usage("Perfect. I can explain.", "transfer_ready", "pardon?", False, is_confused=True) == "I can explain."
    
    # After objections -> Should strip
    assert policy.clean_perfect_usage("Perfect. That's fine.", "transfer_ready", "I'm busy", True) == "That's fine."


def test_dialogue_style_controller() -> None:
    controller = DialogueStyleController()
    
    # Markdown stripping
    text_md = "**Hello** # Title \n- Bullet\n`code`"
    assert controller.process(text_md, "opening") == "Hello Title Bullet code"
    
    # Corporate phrase cleaning
    assert controller.clean_corporate_phrases("As an AI assistant, I can help. Absolutely!") == "I can help. yes!"
    
    # One question enforcement
    q_text = "How is your day? Are you looking for options to cover burial expenses? Do you have time?"
    cleaned_q = controller.enforce_one_question(q_text, "interest_check")
    assert "Are you looking for options to cover burial expenses?" in cleaned_q
    assert "Do you have time." in cleaned_q
    assert cleaned_q.count("?") == 1
    
    # Brevity enforcement
    long_text = "Okay, great. The next step is really simple. We just need to check your age to make sure you qualify. Are you between forty and eighty-five?"
    brevity_text = controller.enforce_brevity(long_text, "age_range")
    assert len(brevity_text.split()) <= 25
    assert "Are you between forty and eighty-five?" in brevity_text


def test_repetition_guard() -> None:
    guard = RepetitionGuard()
    
    # Sentence duplicate
    assert guard.filter_response("Hello. Hello.") == "Hello."
    
    # Opener duplicate
    guard = RepetitionGuard()
    assert guard.filter_response("Sure, I can help.") == "Sure, I can help."
    assert guard.filter_response("Sure, I can help. What is your age?") == "What is your age?"
    
    # Acknowledgment counts
    guard = RepetitionGuard()
    assert guard.filter_response("Okay. Let's check.") == "Okay. Let's check."
    assert guard.filter_response("Okay. What is next?") == "What is next?"  # Strips duplicate opener 'Okay.'
    assert guard.filter_response("Okay. Perfect.") == "Perfect."


def test_prosody_controller() -> None:
    controller = ProsodyController()
    
    # Age range
    assert controller.format_for_tts("Age 40-85") == "Age forty to eighty-five"
    assert controller.format_for_tts("forty to eighty-five") == "forty to eighty-five"
    
    # Money
    assert controller.format_for_tts("It costs $50.") == "It costs fifty dollars."
    
    # Percent
    assert controller.format_for_tts("Get 20% off.") == "Get twenty percent off."
    
    # Time
    assert controller.format_for_tts("At 3:30 today.") == "At three thirty today."
    assert controller.format_for_tts("At 5:00.") == "At five o'clock."
    
    # Phone number
    assert controller.format_for_tts("+13055550199") == "one three zero five five five five zero one nine nine"
    
    # Standalone number vs alphanumeric ID
    assert controller.format_for_tts("I have 5 options.") == "I have five options."
    # Protected ID check
    assert controller.format_for_tts("id: call-x-123 agent-9") == "id: call-x-123 agent-9"
    
    # Punctuation/symbols
    assert controller.format_for_tts("this; that & another @ place") == "this. that and another at place"
    
    # Splits compound sentences
    assert controller.format_for_tts("Yes, but we should go.") == "Yes. But we should go."


def test_spoken_output_auditor() -> None:
    auditor = SpokenOutputAuditor()
    
    # Compliant text
    assert len(auditor.audit("Are you between forty and eighty-five?", "age_range")) == 0
    
    # Violations
    violations = auditor.audit(
        "As an AI assistant, I can quote you $50. Do you want to see if you qualify? "
        "How are you? I want to make sure we give you the absolute best options today since we have many plans available for you.", 
        "age_range"
    )
    assert any("AI/chatbot disclosure" in v for v in violations)
    assert any("Price quote" in v for v in violations)
    assert any("Too many questions" in v for v in violations)
    assert any("Brevity violation" in v for v in violations)


@pytest.fixture
def runtime(tmp_path: Path) -> AgentRuntime:
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
async def test_integration_happy_path(runtime: AgentRuntime) -> None:
    # 1. Opening Stage -> Transitions to interest_check
    result = await runtime.process_turn("Hello?")
    assert result.stage == "interest_check"
    # No backchannel on opening/interest_check transition
    assert not result.agent_response.startswith("Okay.")
    
    # 2. Interest Check -> age_range
    # Previous turn had no backchannel, so backchannel policy should choose one here
    result2 = await runtime.process_turn("Sure, I am open to looking at some options.")
    assert result2.stage == "age_range"
    # Backchannel prepended (deterministic based on turn index)
    assert any(result2.agent_response.startswith(bc) for bc in ["Okay.", "Gotcha.", "Fair enough.", "That makes sense.", "Understood.", "Right."])


@pytest.mark.asyncio
async def test_integration_objection(runtime: AgentRuntime) -> None:
    runtime.state_machine.call_state.transition_to(CallStage.INTEREST_CHECK)
    
    result = await runtime.process_turn("I don't need this, I'm too busy.")
    # Objection should increase objection count
    assert runtime.state_machine.call_state.objection_count == 1
    # Objection handled, "Perfect" should not be present
    assert "perfect" not in result.agent_response.lower()


@pytest.mark.asyncio
async def test_integration_dnc_fallback(runtime: AgentRuntime) -> None:
    # DNC request should use fallback
    result = await runtime.process_turn("Put me on the do not call list.")
    assert result.stage == "dnc"
    assert result.should_end_call is True
    # Verify fallback message used
    assert "remove" in result.agent_response.lower() or "do not call" in result.agent_response.lower()


@pytest.mark.asyncio
async def test_integration_silence(runtime: AgentRuntime) -> None:
    runtime.state_machine.call_state.transition_to(CallStage.AGE_RANGE)
    # User is silent
    result = await runtime.process_turn("silence")
    # Silence shouldn't have backchannels prepended
    for bc in ["Okay.", "Gotcha.", "Fair enough.", "That makes sense.", "Understood.", "Right."]:
        assert not result.agent_response.startswith(bc)
