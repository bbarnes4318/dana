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
