"""Tests for DirectResponseController concurrency, final intent, and hostile refusal behaviors."""

import asyncio
import os
import sys
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dana.runtime.direct_response_controller import (
    DirectResponseController,
    DirectResponsePolicy,
    TurnPolicy,
    compute_similarity,
)
from core.intent.short_response_intent import classify_intent
from dana.config.voice_config import VoiceConfig


class FakeConfig(VoiceConfig):
    pass


class FakePlayoutHandle:
    async def wait_for_playout(self):
        await asyncio.sleep(0)


class FakeSession:
    def __init__(self):
        self.say_calls = []

    def say(self, text: str) -> FakePlayoutHandle:
        self.say_calls.append(text)
        return FakePlayoutHandle()

    def interrupt(self):
        pass


class FakeRuntimeResult:
    def __init__(self, agent_response="", should_end_call=False, stage="opening"):
        self.agent_response = agent_response
        self.should_end_call = should_end_call
        self.stage = stage


class FakeAdapter:
    def __init__(self):
        self.call_id = "test-call-id"
        self.prompt_loader = MagicMock()
        self.prompt_loader.build_system_prompt.return_value = "You are Dana."
        self._should_end = False
        self._stage = "opening"

    async def process_user_turn(self, user_text, chat_fn, interrupted=False):
        # Fake adapter processing
        # In a real test we can mock what adapter returns
        return FakeRuntimeResult(
            agent_response="Understood. I won’t keep you. Take care.",
            should_end_call=self._should_end,
            stage=self._stage,
        )


class FakeAgent:
    def __init__(self):
        self.current_turn_response = ""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.should_disconnect = False
        self.warm_bridge_active = False
        self.fallback_disconnect_task = None
        self.llm = AsyncMock()


class FakeRoom:
    def __init__(self):
        self._connected = True

    def isconnected(self):
        return self._connected

    async def disconnect(self):
        self._connected = False


def make_event(text: str):
    ev = MagicMock()
    ev.transcript = text
    ev.is_final = True
    return ev


@pytest.fixture
def controller_setup():
    config = FakeConfig()
    config.direct_response_echo_similarity_threshold = 0.85
    session = FakeSession()
    agent = FakeAgent()
    adapter = FakeAdapter()
    room = FakeRoom()
    logger = MagicMock()

    controller = DirectResponseController(
        session=session,
        agent=agent,
        adapter=adapter,
        latency_recorder=MagicMock(),
        room=room,
        config=config,
        log=logger,
    )
    return controller, session, agent, adapter, logger


@pytest.mark.asyncio
async def test_controller_start_is_idempotent(controller_setup):
    controller, _, _, _, logger = controller_setup
    
    # Start first time
    await controller.start()
    assert controller._running is True
    assert controller._consumer_task is not None
    
    # Start second time
    first_task = controller._consumer_task
    await controller.start()
    
    # Check it logged the idempotency warning and kept the same task
    logger.info.assert_any_call("DIRECT_CONTROLLER_ALREADY_STARTED")
    assert controller._consumer_task is first_task
    
    await controller.stop()


def test_go_fuck_yourself_classifies_hostile_refusal():
    hostile_phrases = [
        "go fuck yourself",
        "fuck off",
        "fuck you",
        "piss off",
        "shut up",
        "go to hell",
        "quit calling",
        "stop fucking calling",
    ]
    for phrase in hostile_phrases:
        assert classify_intent(phrase) == "hostile_refusal"
        # Test leading/trailing whitespace
        assert classify_intent(f"  {phrase}  ") == "hostile_refusal"


def test_hostile_refusal_policy_ends_call():
    config = FakeConfig()
    policy_checker = DirectResponsePolicy(config)
    
    policy = policy_checker.get_turn_policy("interest_check", "go fuck yourself")
    assert policy.max_tokens <= 40
    assert policy.should_end_after_response is True


def test_hostile_refusal_has_no_sales_question():
    config = FakeConfig()
    policy_checker = DirectResponsePolicy(config)
    
    policy = policy_checker.get_turn_policy("interest_check", "go fuck yourself")
    assert "Do NOT ask any question" in policy.instruction_suffix
    assert "Understood. I won’t keep you. Take care." in policy.instruction_suffix


@pytest.mark.asyncio
async def test_final_intent_clears_queue(controller_setup):
    controller, session, agent, adapter, logger = controller_setup
    
    # Mock process_user_turn to sleep for 0.1s
    async def delayed_process(user_text, chat_fn, interrupted=False):
        await asyncio.sleep(0.1)
        return FakeRuntimeResult(agent_response="Hello", should_end_call=False)
        
    adapter.process_user_turn = delayed_process
    await controller.start()

    # Enqueue a couple of normal turns first
    controller.handle_transcription_event(make_event("hello"))
    controller.handle_transcription_event(make_event("are you there"))
    assert controller._queue.qsize() == 2

    # Now enqueue a final hostile refusal intent
    controller.handle_transcription_event(make_event("go fuck yourself"))

    # Let the consumer loop process
    await asyncio.sleep(0.3)

    # Queue should be completely cleared after processing final intent
    assert controller._queue.qsize() == 0
    logger.info.assert_any_call("DIRECT_FINAL_INTENT_DETECTED")
    logger.info.assert_any_call("DIRECT_QUEUE_CLEARED_FOR_FINAL_INTENT")
    
    await controller.stop()


@pytest.mark.asyncio
async def test_post_end_transcripts_ignored(controller_setup):
    controller, session, agent, adapter, logger = controller_setup
    await controller.start()

    # Trigger final intent
    controller.handle_transcription_event(make_event("go fuck yourself"))
    await asyncio.sleep(0.05)

    assert controller._ending_call is True
    assert controller._ended_call is True

    # Try to send a post-end transcript
    controller.handle_transcription_event(make_event("wait come back"))
    
    logger.info.assert_any_call("DIRECT_POST_END_TURN_IGNORED")
    assert controller._queue.qsize() == 0

    await controller.stop()


def test_near_duplicate_final_transcript_suppressed(controller_setup):
    controller, _, _, _, logger = controller_setup
    controller._running = True

    # Send first transcript
    controller.handle_transcription_event(make_event("hello my name is alex"))
    assert controller._queue.qsize() == 1

    # Send a near duplicate within window
    controller.handle_transcription_event(make_event("hello my name is alex!"))
    
    logger.info.assert_any_call("DIRECT_DUPLICATE_FINAL_TRANSCRIPT_SUPPRESSED")
    # Queue size should still be 1 (second was suppressed)
    assert controller._queue.qsize() == 1
