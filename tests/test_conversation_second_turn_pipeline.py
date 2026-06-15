import pytest
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from main import DanaAgent, SharedComponents
from telephony.livekit_adapter import LiveKitOutboundAdapter, LiveKitDialConfig
from latency_metrics import LatencyRecorder
from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
from storage.repository import Repository
from voice_config import VoiceConfig
from livekit.agents import llm

class DummyChoiceDelta:
    def __init__(self, role: str = "", content: str = ""):
        self.role = role
        self.content = content

class DummyChatChunk:
    def __init__(self, id: str = "", delta: DummyChoiceDelta = None, **kwargs):
        self.id = id
        self.delta = delta or DummyChoiceDelta()

class MockAdapter:
    def __init__(self, call_id):
        self.call_id = call_id
        self.last_streaming_result = None
        self.state_machine = MagicMock()
        self.state_machine.call_state.current_stage.value = "interest_check"
        self.runtime = MagicMock()
        self.runtime.conversational_timing.get_pre_speech_delay.return_value = 0.0

    async def process_user_turn_stream(self, user_text, chat_stream_fn, latency_recorder=None, interrupted=False):
        from core.agent_runtime import RuntimeResult
        result = RuntimeResult(
            agent_response="I can hear you clearly.",
            stage="interest_check",
            should_end_call=False,
            compliance_ok=True
        )
        self.last_streaming_result = result
        yield DummyChatChunk(delta=DummyChoiceDelta(role="assistant", content=""))
        yield DummyChatChunk(delta=DummyChoiceDelta(role="assistant", content="I can hear you clearly."))

    async def convert_response_to_stream(self, text, *args, **kwargs):
        yield DummyChatChunk(delta=DummyChoiceDelta(role="assistant", content=""))
        yield DummyChatChunk(delta=DummyChoiceDelta(role="assistant", content=text))

@pytest.fixture(autouse=True)
def setup_sdk_mocks(monkeypatch):
    monkeypatch.setattr(llm, "ChatChunk", DummyChatChunk)
    monkeypatch.setattr(llm, "ChoiceDelta", DummyChoiceDelta)

@pytest.mark.asyncio
async def test_second_turn_pipeline_integration(tmp_path, monkeypatch):
    """
    Build a mocked AgentSession-style flow that simulates:
    - participant joins
    - user says: "Yes, I can hear you" (ChatMessage.content is a string)
    - final transcript event arrives
    - llm_node receives the user text
    - AgentRuntime returns a response (mocked via adapter)
    - tts_node receives response text
    - at least one audio frame is emitted
    """
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_ENABLE_AMD_WORKER", "false")
    monkeypatch.setenv("DANA_ENABLE_STREAMING_RESPONSE", "true")
    
    # 1. Initialize Repository
    repo = Repository(data_dir=tmp_path)
    
    # 2. Configure SharedComponents
    config = VoiceConfig()
    shared = SharedComponents(config)
    shared.repository = repo
    shared.vad = MagicMock()
    shared.stt = MagicMock()
    
    mock_llm = MagicMock(spec=llm.LLM)
    shared.llm = mock_llm
    
    mock_tts = MagicMock()
    mock_stream = MagicMock()
    def mock_stream_iter(*args, **kwargs):
        async def _gen():
            await asyncio.sleep(0.01)
            from livekit import rtc
            frame = rtc.AudioFrame(data=b"\x00\x00" * 320, sample_rate=16000, num_channels=1, samples_per_channel=320)
            class MockEvent:
                def __init__(self, f):
                    self.frame = f
            yield MockEvent(frame)
        return _gen()
    mock_stream.__aiter__ = mock_stream_iter
    mock_stream.interrupt = AsyncMock()
    mock_stream.aclose = AsyncMock()
    mock_tts.stream.return_value = mock_stream
    shared.tts = mock_tts

    # 3. Create LatencyRecorder & DanaAgent
    latency_recorder = LatencyRecorder("test-integration-call")
    agent = DanaAgent(shared, latency_recorder)
    
    # Set helper adapter
    agent.adapter = MockAdapter("test-integration-call")
    
    # Pre-mark early loop stages
    latency_recorder.mark("room_joined")
    latency_recorder.mark("participant_joined")
    latency_recorder.mark("inbound_audio_frame_received")
    latency_recorder.mark("vad_start_of_speech")
    latency_recorder.mark("vad_end_of_speech")
    latency_recorder.mark("stt_stream_created")
    latency_recorder.mark("transcript_final")
    latency_recorder.mark("greeting_tts_started")
    
    # 4. User turn with ChatMessage.content as a string
    class MockMessage:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    chat_ctx = MagicMock()
    chat_ctx.messages = [MockMessage(role="user", content="Yes, I can hear you")]
    
    chunks = []
    async for chunk in agent.llm_node(chat_ctx, [], None):
        chunks.append(chunk)
        
    assert len(chunks) > 0
    
    # Gather responses from chunks
    response_text = "".join(c.delta.content for c in chunks if c.delta.content)
    assert "hear you" in response_text
    
    async def text_stream():
        for c in chunks:
            if c.delta.content:
                yield c.delta.content
                
    frames = []
    async for frame in agent.tts_node(text_stream(), None):
        frames.append(frame)
        
    assert len(frames) > 0
    assert not agent.should_disconnect
    
    # Verify timeline markers
    events = latency_recorder.events
    assert "llm_node_entered" in events
    assert "user_text_seen_by_llm_node" in events
    assert "agent_response_text_created" in events
    assert "tts_first_text" in events
    assert "tts_first_audio" in events
    assert "second_turn_audio_published" in events

    await repo.close()

@pytest.mark.asyncio
async def test_llm_node_responds_when_content_is_list_of_text_parts(tmp_path, monkeypatch):
    """Verify llm_node correctly extracts user text when content is a list of text parts."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_ENABLE_STREAMING_RESPONSE", "true")
    
    repo = Repository(data_dir=tmp_path)
    config = VoiceConfig()
    shared = SharedComponents(config)
    shared.repository = repo
    shared.vad = MagicMock()
    shared.stt = MagicMock()
    shared.tts = MagicMock()
    
    latency_recorder = LatencyRecorder("test-list-parts")
    agent = DanaAgent(shared, latency_recorder)
    agent.adapter = MockAdapter("test-list-parts")
    
    # Create a ChatMessage where content is a list of parts
    class MockPart:
        def __init__(self, text):
            self.text = text
            
    class MockMessage:
        def __init__(self, role, parts_list):
            self.role = role
            self.content = parts_list

    chat_ctx = MagicMock()
    msg = MockMessage(role="user", parts_list=[MockPart("Hello"), MockPart("world")])
    chat_ctx.messages = [msg]
    
    chunks = []
    async for chunk in agent.llm_node(chat_ctx, [], None):
        chunks.append(chunk)
        
    assert len(chunks) > 0
    response_text = "".join(c.delta.content for c in chunks if c.delta.content)
    assert len(response_text) > 0
    assert "user_text_seen_by_llm_node" in latency_recorder.events
    
    await repo.close()

@pytest.mark.asyncio
async def test_llm_node_responds_with_recovery_text_on_failure(tmp_path, monkeypatch):
    """Verify llm_node falls back to recovery text when extraction fails, and doesn't silently return empty."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_ENABLE_STREAMING_RESPONSE", "true")
    
    repo = Repository(data_dir=tmp_path)
    config = VoiceConfig()
    shared = SharedComponents(config)
    shared.repository = repo
    shared.vad = MagicMock()
    shared.stt = MagicMock()
    shared.tts = MagicMock()
    
    latency_recorder = LatencyRecorder("test-empty-fail")
    agent = DanaAgent(shared, latency_recorder)
    agent.adapter = MockAdapter("test-empty-fail")
    
    # Message content is empty / None
    class EmptyMessage:
        def __init__(self):
            self.role = "user"
            self.content = None
            
    chat_ctx = MagicMock()
    chat_ctx.messages = [EmptyMessage()]
    
    chunks = []
    async for chunk in agent.llm_node(chat_ctx, [], None):
        chunks.append(chunk)
        
    assert len(chunks) > 0
    response_text = "".join(c.delta.content for c in chunks if c.delta.content)
    
    # Assert recovery text is returned
    assert "catch that" in response_text or "more time" in response_text
    
    # Assert markers
    assert "llm_no_user_text" in latency_recorder.events
    assert "agent_response_text_created" in latency_recorder.events
    
    await repo.close()
