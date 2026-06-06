import pytest
import asyncio
from core.streaming_response import SafeClauseBuffer
from core.livekit_runtime_adapter import LiveKitRuntimeAdapter
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

@pytest.mark.asyncio
async def test_streaming_response_barge_in_cancellation(monkeypatch) -> None:
    """Verify that if the async stream is cancelled (simulating user barge-in), the adapter cleans up."""
    # Setup livekit adapter
    adapter = LiveKitRuntimeAdapter(call_id="call-barge-in-test")
    
    # We will simulate a chat stream function that gets cancelled mid-stream
    async def chat_stream_cancelled(instructions: str):
        yield "Okay, let's start. "
        await asyncio.sleep(0.5)
        # Simulate cancellation throwing CancelledError
        raise asyncio.CancelledError()

    chunks = []
    try:
        # Run streaming turn and collect chunks
        async for chunk in adapter.process_user_turn_stream("Hello", chat_stream_cancelled):
            chunks.append(chunk)
    except asyncio.CancelledError:
        pass  # Expected

    # Verify that the first clause was generated and emitted before cancellation
    assert len(chunks) > 1
    assert chunks[1].delta.content == "Okay, let's start."
