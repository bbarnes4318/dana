import pytest
import os
import asyncio
from pathlib import Path
from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
from core.call_state import CallStage
from latency_metrics import LatencyRecorder
from livekit.agents import llm

class DummyChoiceDelta:
    def __init__(self, role: str = "", content: str = ""):
        self.role = role
        self.content = content

class DummyChatChunk:
    def __init__(self, id: str = "", delta: DummyChoiceDelta = None, **kwargs):
        self.id = id
        self.delta = delta or DummyChoiceDelta()

@pytest.fixture(autouse=True)
def setup_mocks(monkeypatch):
    monkeypatch.setattr(llm, "ChatChunk", DummyChatChunk)
    monkeypatch.setattr(llm, "ChoiceDelta", DummyChoiceDelta)

@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parent.parent

@pytest.mark.asyncio
async def test_livekit_runtime_adapter_streaming_happy_path(project_root: Path, monkeypatch) -> None:
    """Verify happy path streaming splits safe clause and marks latency recorder."""
    monkeypatch.setenv("DANA_ENABLE_STREAMING_RESPONSE", "true")
    
    adapter = LiveKitRuntimeAdapter(call_id="call-stream-happy", project_root=project_root)
    latency_rec = LatencyRecorder("call-stream-happy")
    latency_rec.mark("llm_request_start")

    # Mock LLM stream that yields tokens
    async def mock_stream_fn(inst: str):
        yield "Okay, "
        yield "that "
        yield "makes "
        yield "sense. "
        yield "We "
        yield "can "
        yield "help."

    chunks = []
    async for chunk in adapter.process_user_turn_stream("Yes", mock_stream_fn, latency_recorder=latency_rec):
        chunks.append(chunk)

    # 1. Check content of chunk streams
    # First chunk role establishment
    assert chunks[0].delta.role == "assistant"
    # Second chunk contains first clause
    assert chunks[1].delta.content == "Okay, that makes sense."
    # Third chunk contains remainder
    assert chunks[2].delta.content == "We can help."

    # 2. Check latency metrics
    latency_summary = latency_rec.to_dict()
    assert latency_rec.events.get("first_safe_clause_detected") is not None
    assert latency_rec.events.get("first_streamed_tts_text") is not None
    assert "first_safe_clause_ms" in latency_summary["durations"]
    assert "first_streamed_tts_text_ms" in latency_summary["durations"]

@pytest.mark.asyncio
async def test_livekit_runtime_adapter_streaming_unsafe_fallback(project_root: Path, monkeypatch) -> None:
    """Verify that unsafe streaming response chunks are withheld or trigger fallback recovery."""
    monkeypatch.setenv("DANA_ENABLE_STREAMING_RESPONSE", "true")

    # Case 1: First clause is safe, remainder is unsafe
    # The first clause should be emitted, but the unsafe remainder must be withheld
    adapter1 = LiveKitRuntimeAdapter(call_id="call-stream-unsafe-1", project_root=project_root)
    async def mock_stream_fn_1(inst: str):
        yield "Sure. "
        yield "You "
        yield "are "
        yield "approved."

    chunks1 = []
    async for chunk in adapter1.process_user_turn_stream("Yes", mock_stream_fn_1):
        chunks1.append(chunk)

    content_emitted1 = "".join(chunk.delta.content for chunk in chunks1 if chunk.delta.content)
    assert "sure" in content_emitted1.lower()
    assert "approved" not in content_emitted1.lower()

    # Case 2: Very first clause is unsafe
    # Since nothing was emitted yet, it falls back to full-response validation path and yields fallback response
    adapter2 = LiveKitRuntimeAdapter(call_id="call-stream-unsafe-2", project_root=project_root)
    async def mock_stream_fn_2(inst: str):
        yield "You "
        yield "are "
        yield "approved "
        yield "today."

    chunks2 = []
    async for chunk in adapter2.process_user_turn_stream("Yes", mock_stream_fn_2):
        chunks2.append(chunk)

    content_emitted2 = "".join(chunk.delta.content for chunk in chunks2 if chunk.delta.content)
    assert "approved" not in content_emitted2.lower()
    assert "license" in content_emitted2.lower() or "sorry" in content_emitted2.lower() or "perfect" in content_emitted2.lower()

@pytest.mark.asyncio
async def test_livekit_runtime_adapter_streaming_terminal_short_circuit(project_root: Path, monkeypatch) -> None:
    """Verify that terminal stage transitions (e.g. DNC) short-circuit immediately without calling LLM."""
    monkeypatch.setenv("DANA_ENABLE_STREAMING_RESPONSE", "true")

    adapter = LiveKitRuntimeAdapter(call_id="call-stream-terminal", project_root=project_root)
    
    chat_called = False
    async def mock_stream_fn(inst: str):
        nonlocal chat_called
        chat_called = True
        yield "Should not be called"

    # Process user turn with DNC command
    chunks = []
    async for chunk in adapter.process_user_turn_stream("Do not call me again. Put me on DNC.", mock_stream_fn):
        chunks.append(chunk)

    # 1. LLM should NOT have been called
    assert chat_called is False
    # 2. Result should show end call and transition to DNC stage
    res = adapter.last_streaming_result
    assert res is not None
    assert res.should_end_call is True
    assert res.stage == "dnc"
    assert "note of that" in "".join(chunk.delta.content for chunk in chunks).lower()
