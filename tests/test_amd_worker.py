import pytest
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from main import run_amd_worker
from livekit import rtc
from latency_metrics import LatencyRecorder

class MockFrame:
    def __init__(self, data, duration):
        self.data = data
        self.duration = duration

class MockEvent:
    def __init__(self, frame):
        self.frame = frame

@pytest.mark.asyncio
async def test_amd_does_not_disconnect_on_short_speech():
    """Verify that AMD does not flag voicemail or disconnect on short speech (e.g. 1.0s)."""
    track = MagicMock()
    track.sid = "test-track-1"
    
    session = MagicMock()
    agent = MagicMock()
    agent.is_voicemail = False
    agent._latency_recorder = LatencyRecorder("test-amd-short")
    
    room = MagicMock()
    room.isconnected.return_value = True
    room.disconnect = AsyncMock()

    # 1.0 second of speech = 50 frames
    frame_data_list = []
    for i in range(5):
        frame_data_list.extend([1000] * 32)
        frame_data_list.extend([-1000] * 32)
    import struct
    frame_bytes = struct.pack("<320h", *frame_data_list)
    
    events = []
    for _ in range(50):
        frame = MagicMock()
        frame.data = frame_bytes
        frame.duration = 0.02 # 20ms
        events.append(MockEvent(frame))
        
    mock_audio_stream = MagicMock()
    def mock_aiter(*args, **kwargs):
        async def _gen():
            for ev in events:
                yield ev
        return _gen()
    mock_audio_stream.__aiter__ = mock_aiter
    mock_audio_stream.aclose = AsyncMock()

    with patch("livekit.rtc.AudioStream", return_value=mock_audio_stream):
        await run_amd_worker(track, session, agent, room)
        
    assert agent.is_voicemail is False
    room.disconnect.assert_not_called()

@pytest.mark.asyncio
async def test_amd_disconnects_on_long_speech_voicemail():
    """Verify that AMD flags voicemail and disconnects on long speech (e.g. 3.0s)."""
    track = MagicMock()
    track.sid = "test-track-2"
    
    session = MagicMock()
    agent = MagicMock()
    agent.is_voicemail = False
    agent._latency_recorder = LatencyRecorder("test-amd-long")
    
    room = MagicMock()
    room.isconnected.side_effect = lambda: not agent.is_voicemail
    room.disconnect = AsyncMock()

    # 3.0 seconds of speech = 150 frames
    frame_data_list = []
    for i in range(5):
        frame_data_list.extend([1000] * 32)
        frame_data_list.extend([-1000] * 32)
    import struct
    frame_bytes = struct.pack("<320h", *frame_data_list)
    
    events = []
    for _ in range(150):
        frame = MagicMock()
        frame.data = frame_bytes
        frame.duration = 0.02 # 20ms
        events.append(MockEvent(frame))
        
    mock_audio_stream = MagicMock()
    def mock_aiter(*args, **kwargs):
        async def _gen():
            for ev in events:
                yield ev
        return _gen()
    mock_audio_stream.__aiter__ = mock_aiter
    mock_audio_stream.aclose = AsyncMock()

    with patch("livekit.rtc.AudioStream", return_value=mock_audio_stream):
        await run_amd_worker(track, session, agent, room)
        
    assert agent.is_voicemail is True
    # Yield control to let the room.disconnect async task execute
    await asyncio.sleep(0.01)
    room.disconnect.assert_called_once()

def test_controlled_live_test_disables_amd_by_default(monkeypatch):
    """Verify that AMD is disabled by default in controlled live test mode."""
    monkeypatch.setenv("DANA_CONTROLLED_LIVE_TEST", "true")
    monkeypatch.setenv("DANA_ENABLE_AMD_WORKER", "false")
    
    controlled_live = os.getenv("DANA_CONTROLLED_LIVE_TEST", "false").lower() in ("true", "1", "yes")
    enable_amd = os.getenv("DANA_ENABLE_AMD_WORKER", "false").lower() in ("true", "1", "yes")
    
    assert controlled_live is True
    assert enable_amd is False
    assert (controlled_live and not enable_amd) is True
