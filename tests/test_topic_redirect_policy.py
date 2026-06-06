"""Tests for the TopicRedirectPolicy and its integration with AgentRuntime."""

from __future__ import annotations

from pathlib import Path
import pytest

from core.call_state import CallStage
from core.state_machine import StateMachine
from core.objection_classifier import ObjectionClassifier
from core.objection_response_policy import ObjectionResponsePolicy
from core.prompt_loader import PromptLoader
from rag.context_builder import ContextBuilder
from core.action_policy import ActionPolicy
from tools.tool_registry import ToolRegistry
from safety.compliance_filter import ComplianceFilter
from safety.output_validator import OutputValidator
from safety.call_stop_policy import CallStopPolicy
from safety.pii_redaction import PIIRedactor
from storage.repository import Repository
from core.agent_runtime import AgentRuntime
from safety.topic_redirect_policy import TopicRedirectPolicy


@pytest.fixture
def policy() -> TopicRedirectPolicy:
    return TopicRedirectPolicy()


@pytest.fixture
def runtime(tmp_path: Path) -> AgentRuntime:
    project_root = Path(__file__).resolve().parent.parent
    loader = PromptLoader(project_root=project_root)
    sm = StateMachine()
    classifier = ObjectionClassifier(confidence_threshold=0.3)
    objection_policy = ObjectionResponsePolicy()
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
        objection_policy=objection_policy,
        context_builder=cb,
        action_policy=action_policy,
        tool_registry=registry,
        compliance_filter=comp_filter,
        output_validator=validator,
        call_stop_policy=stop_policy,
        pii_redactor=redactor,
        repository=repo,
    )


@pytest.mark.parametrize(
    "utterance,expected_category",
    [
        # Politics
        ("what do you think about Trump?", "politics"),
        ("who is going to win the election?", "politics"),
        ("I vote republican", "politics"),
        # Weather
        ("how is the weather over there?", "weather"),
        ("is it raining outside?", "weather"),
        ("what's the temperature?", "weather"),
        # Sports
        ("did you see the football game last night?", "sports"),
        ("who is your favorite NBA team?", "sports"),
        ("nfl scores today", "sports"),
        # Personal
        ("are you married?", "personal"),
        ("how old are you?", "personal"),
        ("where do you live?", "personal"),
        # Jokes
        ("tell me a joke", "jokes"),
        ("do you know any jokes?", "jokes"),
        # AI / Bot
        ("are you a robot?", "ai_bot"),
        ("is this a recording?", "ai_bot"),
        ("are you a real human?", "ai_bot"),
        # Irrelevant
        ("what is your favorite movie?", "irrelevant"),
        ("do you like traveling?", "irrelevant"),
        ("what's the best recipe for pasta?", "irrelevant"),
    ],
)
def test_detect_divergent_topics(policy: TopicRedirectPolicy, utterance: str, expected_category: str) -> None:
    assert policy.detect_divergent_topic(utterance) == expected_category


def test_no_divergent_topic(policy: TopicRedirectPolicy) -> None:
    assert policy.detect_divergent_topic("Hello, is this American Beneficiary?") is None
    assert policy.detect_divergent_topic("Yes, I'm forty-five years old.") is None
    assert policy.detect_divergent_topic("") is None
    assert policy.detect_divergent_topic("   ") is None


def test_redirect_responses_are_one_sentence(policy: TopicRedirectPolicy) -> None:
    for stage in CallStage:
        resp = policy.get_redirect_response(stage)
        assert resp.count(".") + resp.count("?") + resp.count("!") == 1
        assert len(resp.split()) > 0


@pytest.mark.asyncio
async def test_runtime_sync_redirect_short_circuit(runtime: AgentRuntime) -> None:
    runtime.state_machine.call_state.transition_to(CallStage.AGE_RANGE)
    current_stage = runtime.state_machine.call_state.current_stage

    # We provide a dummy chat_fn that increments a counter when called.
    llm_calls = 0
    async def chat_fn(instructions: str) -> str:
        nonlocal llm_calls
        llm_calls += 1
        return "I am the LLM response."

    # Process divergent topic
    result = await runtime.process_turn("What is the weather like?", chat_fn=chat_fn)
    
    # Assertions
    assert result.stage == current_stage.value  # Stage must NOT transition
    assert result.should_end_call is False      # Call should NOT end
    assert result.compliance_ok is True
    assert llm_calls == 0  # LLM must NOT be called

    # Expected response from redirect policy
    expected_response = runtime.topic_redirect_policy.get_redirect_response(current_stage)
    assert result.agent_response == expected_response


@pytest.mark.asyncio
async def test_runtime_streaming_redirect_short_circuit(runtime: AgentRuntime) -> None:
    runtime.state_machine.call_state.transition_to(CallStage.INTEREST_CHECK)
    current_stage = runtime.state_machine.call_state.current_stage

    # Prepare streaming turn
    instructions, short_circuit_res = await runtime.prepare_turn("Are you a robot?")
    
    # Assertions
    assert instructions is None  # instructions must be None for short circuit
    assert short_circuit_res is not None
    assert short_circuit_res.stage == current_stage.value  # Stage must NOT transition
    assert short_circuit_res.should_end_call is False

    expected_response = runtime.topic_redirect_policy.get_redirect_response(current_stage)
    assert short_circuit_res.agent_response == expected_response
