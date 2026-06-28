"""Tests for DirectResponseController integration behavior."""

import asyncio
import os
import sys
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dana.runtime.direct_response_controller import (
    DirectResponseController,
    DirectResponsePolicy,
    TurnPolicy,
    extract_transcript_text,
)
from dana.config.voice_config import VoiceConfig


# ---------------------------------------------------------------------------
# Fakes / Mocks
# ---------------------------------------------------------------------------

class FakeConfig(VoiceConfig):
    """VoiceConfig subclass with test-friendly defaults."""
    pass


class FakePlayoutHandle:
    """Fake handle returned by session.say()."""

    async def wait_for_playout(self):
        await asyncio.sleep(0)


class FakeSession:
    """Fake LiveKit AgentSession for testing."""

    def __init__(self):
        self.history = FakeHistory()
        self.say_calls = []

    def say(self, text: str) -> FakePlayoutHandle:
        self.say_calls.append(text)
        return FakePlayoutHandle()

    def interrupt(self):
        pass


class FakeHistory:
    """Fake ChatHistory."""

    def __init__(self):
        self._messages = []

    @property
    def messages(self):
        return list(self._messages)

    def add_message(self, role: str, content: str):
        msg = MagicMock()
        msg.role = role
        msg.content = content
        msg.text_content = content
        self._messages.append(msg)


class FakeRuntimeResult:
    def __init__(self, agent_response="", should_end_call=False, stage="opening"):
        self.agent_response = agent_response
        self.should_end_call = should_end_call
        self.stage = stage
        self.pre_speech_delay = 0.0
        self.extracted_data = {}
        self.tool_results = []
        self.compliance_ok = True


class FakeCallState:
    def __init__(self):
        self.current_stage = MagicMock()
        self.current_stage.value = "opening"


class FakeStateMachine:
    def __init__(self):
        self.call_state = FakeCallState()


class FakeAdapter:
    def __init__(self):
        self.state_machine = FakeStateMachine()
        self.call_id = "test-call-id"
        self.prompt_loader = MagicMock()
        self.prompt_loader.build_system_prompt.return_value = "You are Dana."
        self.runtime = MagicMock()
        self.runtime.events = []
        self._process_call_count = 0
        self._last_transcript = None
        self._response_text = "Are you still interested?"

    async def process_user_turn(self, user_text, chat_fn, interrupted=False):
        self._process_call_count += 1
        self._last_transcript = user_text
        return FakeRuntimeResult(agent_response=self._response_text)


class FakeAgent:
    def __init__(self):
        self.current_turn_response = ""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.should_disconnect = False
        self.warm_bridge_active = False
        self.fallback_disconnect_task = None
        self.user_transcript_received = False
        self.llm = MagicMock()
        self.prompt_loader = None


class FakeLatencyRecorder:
    def __init__(self):
        self.events = {}

    def mark(self, name):
        self.events[name] = time.monotonic()


class FakeRoom:
    def __init__(self):
        self._connected = True

    def isconnected(self):
        return self._connected

    async def disconnect(self):
        self._connected = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_event(text: str, is_final: bool = True):
    """Create a fake transcription event."""
    ev = MagicMock()
    ev.transcript = text
    ev.is_final = is_final
    return ev


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestControllerTranscriptFiltering:

    @pytest.fixture
    def setup(self):
        config = FakeConfig()
        session = FakeSession()
        agent = FakeAgent()
        adapter = FakeAdapter()
        latency = FakeLatencyRecorder()
        room = FakeRoom()
        controller = DirectResponseController(
            session=session, agent=agent, adapter=adapter,
            latency_recorder=latency, room=room, config=config,
        )
        return controller, session, agent, adapter

    def test_empty_transcript_not_enqueued(self, setup):
        controller, *_ = setup
        controller.handle_transcription_event(make_event(""))
        assert controller._queue.qsize() == 0

    def test_whitespace_only_not_enqueued(self, setup):
        controller, *_ = setup
        controller.handle_transcription_event(make_event("   "))
        assert controller._queue.qsize() == 0

    def test_valid_transcript_enqueued(self, setup):
        controller, *_ = setup
        controller.handle_transcription_event(make_event("Yes I am interested"))
        assert controller._queue.qsize() == 1

    def test_duplicate_within_window_deduped(self, setup):
        controller, *_ = setup
        controller.handle_transcription_event(make_event("Hello"))
        controller.handle_transcription_event(make_event("Hello"))
        assert controller._queue.qsize() == 1

    def test_duplicate_outside_window_not_deduped(self, setup):
        controller, *_ = setup
        controller.handle_transcription_event(make_event("Hello"))
        # Simulate time passing beyond dedupe window
        controller._last_transcript_time -= 5.0
        controller.handle_transcription_event(make_event("Hello"))
        assert controller._queue.qsize() == 2

    def test_short_garbage_rejected(self, setup):
        controller, *_ = setup
        controller._config.direct_response_min_chars = 2
        controller.handle_transcription_event(make_event("a"))
        assert controller._queue.qsize() == 0


class TestControllerQueueOverflow:

    def test_overflow_drops_oldest(self):
        config = FakeConfig()
        config.direct_response_queue_maxsize = 2
        controller = DirectResponseController(
            session=FakeSession(), agent=FakeAgent(), adapter=FakeAdapter(),
            latency_recorder=FakeLatencyRecorder(), room=FakeRoom(), config=config,
        )
        controller.handle_transcription_event(make_event("First"))
        controller.handle_transcription_event(make_event("Second"))
        # Force the dedupe check to pass by modifying last transcript time
        controller._last_transcript_time -= 5.0
        controller._last_transcript = ""
        controller.handle_transcription_event(make_event("Third"))
        assert controller._queue.qsize() == 2
        # First should have been dropped
        items = []
        while not controller._queue.empty():
            items.append(controller._queue.get_nowait())
        assert "First" not in items
        assert "Third" in items


class TestControllerConsumer:

    @pytest.mark.asyncio
    async def test_consumer_calls_process_user_turn(self):
        config = FakeConfig()
        session = FakeSession()
        agent = FakeAgent()
        adapter = FakeAdapter()
        controller = DirectResponseController(
            session=session, agent=agent, adapter=adapter,
            latency_recorder=FakeLatencyRecorder(), room=FakeRoom(), config=config,
        )
        await controller.start()
        try:
            controller.handle_transcription_event(make_event("Hello"))
            # Give consumer time to process
            await asyncio.sleep(0.1)
            assert adapter._process_call_count == 1
            assert adapter._last_transcript == "Hello"
        finally:
            await controller.stop()

    @pytest.mark.asyncio
    async def test_consumer_calls_session_say(self):
        config = FakeConfig()
        session = FakeSession()
        agent = FakeAgent()
        adapter = FakeAdapter()
        adapter._response_text = "I can help with that!"
        controller = DirectResponseController(
            session=session, agent=agent, adapter=adapter,
            latency_recorder=FakeLatencyRecorder(), room=FakeRoom(), config=config,
        )
        await controller.start()
        try:
            controller.handle_transcription_event(make_event("Tell me more"))
            await asyncio.sleep(0.1)
            assert len(session.say_calls) == 1
            assert "I can help with that!" in session.say_calls[0]
        finally:
            await controller.stop()

    @pytest.mark.asyncio
    async def test_empty_response_uses_fallback(self):
        config = FakeConfig()
        session = FakeSession()
        agent = FakeAgent()
        adapter = FakeAdapter()
        adapter._response_text = ""  # LLM returned nothing
        controller = DirectResponseController(
            session=session, agent=agent, adapter=adapter,
            latency_recorder=FakeLatencyRecorder(), room=FakeRoom(), config=config,
        )
        await controller.start()
        try:
            controller.handle_transcription_event(make_event("Yes"))
            await asyncio.sleep(0.1)
            # Fallback should have been spoken
            assert len(session.say_calls) == 1
            assert len(session.say_calls[0]) > 0
        finally:
            await controller.stop()

    @pytest.mark.asyncio
    async def test_stop_request_triggers_disconnect(self):
        config = FakeConfig()
        session = FakeSession()
        agent = FakeAgent()
        adapter = FakeAdapter()
        adapter._response_text = "I understand, goodbye."
        room = FakeRoom()
        controller = DirectResponseController(
            session=session, agent=agent, adapter=adapter,
            latency_recorder=FakeLatencyRecorder(), room=room, config=config,
        )
        await controller.start()
        try:
            # "stop calling" triggers should_end in policy
            controller.handle_transcription_event(make_event("stop calling me"))
            await asyncio.sleep(0.2)
            # The agent should want to disconnect
            assert agent.should_disconnect is True or getattr(agent, "fallback_disconnect_task", None) is not None
        finally:
            await controller.stop()


class TestControllerBargeIn:

    def test_barge_in_when_agent_speaking(self):
        config = FakeConfig()
        session = MagicMock()
        session.interrupt = MagicMock()
        session.history = FakeHistory()
        controller = DirectResponseController(
            session=session, agent=FakeAgent(), adapter=FakeAdapter(),
            latency_recorder=FakeLatencyRecorder(), room=FakeRoom(), config=config,
        )
        controller._agent_is_speaking = True
        ev = MagicMock()
        ev.new_state = "speaking"
        ev.old_state = "listening"
        controller.handle_user_state_changed(ev)
        # interrupt should be called
        session.interrupt.assert_called_once()


class TestControllerEchoSuppression:

    def test_agent_state_tracks_speaking(self):
        config = FakeConfig()
        controller = DirectResponseController(
            session=FakeSession(), agent=FakeAgent(), adapter=FakeAdapter(),
            latency_recorder=FakeLatencyRecorder(), room=FakeRoom(), config=config,
        )
        ev_start = MagicMock()
        ev_start.new_state = "speaking"
        ev_start.old_state = "listening"
        controller.handle_agent_state_changed(ev_start)
        assert controller._agent_is_speaking is True

        ev_stop = MagicMock()
        ev_stop.new_state = "listening"
        ev_stop.old_state = "speaking"
        controller.handle_agent_state_changed(ev_stop)
        assert controller._agent_is_speaking is False
        assert controller._last_assistant_end_time > 0


class TestControllerLifecycle:

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        config = FakeConfig()
        controller = DirectResponseController(
            session=FakeSession(), agent=FakeAgent(), adapter=FakeAdapter(),
            latency_recorder=FakeLatencyRecorder(), room=FakeRoom(), config=config,
        )
        await controller.start()
        assert controller._running is True
        assert controller._consumer_task is not None
        await controller.stop()
        assert controller._running is False
        assert controller._consumer_task is None

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        config = FakeConfig()
        controller = DirectResponseController(
            session=FakeSession(), agent=FakeAgent(), adapter=FakeAdapter(),
            latency_recorder=FakeLatencyRecorder(), room=FakeRoom(), config=config,
        )
        await controller.start()
        await controller.stop()
        # Calling stop again should not error
        await controller.stop()


@pytest.mark.asyncio
async def test_controller_llm_parameters_and_system_prompt():
    """Verify that process_user_turn calls chat_fn, passing the combined system prompt, max_tokens, and config parameters."""
    config = FakeConfig(
        temperature=0.35,
        top_p=0.88,
        direct_response_max_tokens_default=70,
        direct_response_max_tokens_objection=90,
        direct_response_max_tokens_stop=40,
    )
    session = FakeSession()
    agent = FakeAgent()

    # Mock LLM stream response
    from unittest.mock import AsyncMock
    mock_chunk = MagicMock()
    mock_chunk.delta = MagicMock()
    mock_chunk.delta.content = "Sure, let me help you."

    def fake_chat(*args, **kwargs):
        # Capture parameters passed to agent.llm.chat
        fake_chat.captured_kwargs = kwargs
        fake_chat.captured_chat_ctx = kwargs.get("chat_ctx")

        # Return an async generator yielding the mock chunk
        async def gen():
            yield mock_chunk
        return gen()

    agent.llm.chat = fake_chat

    class TestChatContext:
        def __init__(self):
            self.messages = []

        def add_message(self, role: str, content: str):
            msg = MagicMock()
            msg.role = role
            msg.content = content
            msg.text_content = content
            self.messages.append(msg)

    # We want FakeAdapter to actually call the chat_fn passed to it!
    class CallingFakeAdapter:
        def __init__(self):
            self.state_machine = FakeStateMachine()
            self.call_id = "test-call-id"
            self.prompt_loader = MagicMock()
            self.prompt_loader.build_system_prompt.return_value = "System: You are Dana."
            self.runtime = MagicMock()
            self.runtime.events = []

            # Setup repository mock so query_call_turns returns a fake list of turns
            self.repository = MagicMock()
            self.repository.query_call_turns = AsyncMock(return_value=[
                {"speaker": "user", "text": "Hello", "turn_number": 1},
                {"speaker": "agent", "text": "Hi there!", "turn_number": 2},
            ])

        async def process_user_turn(self, user_text, chat_fn, interrupted=False):
            # Actually call the chat_fn with a dummy instructions text
            response = await chat_fn("Answer the user politely.")
            return FakeRuntimeResult(agent_response=response)

    adapter = CallingFakeAdapter()
    latency = FakeLatencyRecorder()
    room = FakeRoom()

    with patch("livekit.agents.llm.ChatContext", TestChatContext):
        controller = DirectResponseController(
            session=session, agent=agent, adapter=adapter,
            latency_recorder=latency, room=room, config=config,
        )

        # Start controller
        await controller.start()

        # Test 1: Normal progression (max_tokens = 70)
        fake_chat.captured_kwargs = None
        fake_chat.captured_chat_ctx = None
        await controller._process_turn("hello normal turn")

        assert fake_chat.captured_kwargs is not None
        assert fake_chat.captured_kwargs["temperature"] == 0.35
        assert fake_chat.captured_kwargs["top_p"] == 0.88
        assert fake_chat.captured_kwargs["max_tokens"] == 70

        # Check that system prompt contains instruction_suffix
        system_msg = next(m for m in fake_chat.captured_chat_ctx.messages if m.role == "system")
        assert "System: You are Dana." in system_msg.content
        assert "Respond in one short sentence. Ask one clear question." in system_msg.content

        # Check that history turns from repository are copied in order
        user_msgs = [m for m in fake_chat.captured_chat_ctx.messages if m.role == "user"]
        assistant_msgs = [m for m in fake_chat.captured_chat_ctx.messages if m.role == "assistant"]
        assert len(user_msgs) == 1
        assert user_msgs[0].content == "Hello"
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].content == "Hi there!"

        # Test 2: Confusion / objection (max_tokens = 90)
        fake_chat.captured_kwargs = None
        fake_chat.captured_chat_ctx = None
        await controller._process_turn("who is this")
        assert fake_chat.captured_kwargs["max_tokens"] == 90
        system_msg = next(m for m in fake_chat.captured_chat_ctx.messages if m.role == "system")
        assert "Respond in one or two short sentences. Answer the question directly. Do NOT restart the full pitch." in system_msg.content

        # Test 3: Stop / wrong number (max_tokens = 40)
        fake_chat.captured_kwargs = None
        fake_chat.captured_chat_ctx = None
        await controller._process_turn("do not call me again")
        assert fake_chat.captured_kwargs["max_tokens"] == 40
        system_msg = next(m for m in fake_chat.captured_chat_ctx.messages if m.role == "system")
        assert "Respond in ONE polite sentence only. Do NOT ask any question. Acknowledge the request and confirm removal." in system_msg.content

        await controller.stop()

