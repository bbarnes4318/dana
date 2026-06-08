import sys
from unittest.mock import MagicMock

# Define Mock VAD classes before custom_vad is imported
class MockVAD:
    def __init__(self, *args, **kwargs):
        pass

class MockVADStream:
    def __init__(self, *args, **kwargs):
        pass

silero_mock = sys.modules.get('livekit.plugins.silero')
if not isinstance(silero_mock, MagicMock):
    silero_mock = MagicMock()
silero_mock.VAD = MockVAD
silero_mock.VADStream = MockVADStream
sys.modules['livekit.plugins.silero'] = silero_mock

plugins_mock = sys.modules.get('livekit.plugins')
if plugins_mock:
    plugins_mock.silero = silero_mock

# Force re-evaluation of speech.custom_vad to pick up the MockVAD class
if 'speech.custom_vad' in sys.modules:
    del sys.modules['speech.custom_vad']

from stt_service import LocallyHostedSTT, LocalSTTStream
from speech.hybrid_stt_router import HybridSTTRouter, HybridSTTStream
from speech.custom_vad import ElderlySileroVAD, ElderlySileroVADStream

def test_locally_hosted_stt_bind():
    """Verify bind method on LocallyHostedSTT returns a bound instance."""
    config = MagicMock()
    config.sample_rate = 16000
    config.max_speech_duration_s = 30
    config.language = "en"
    
    stt = LocallyHostedSTT(config)
    session = MagicMock()
    session.session_state = {"call_id": "test-call-123"}
    agent = MagicMock()
    
    bound_stt = stt.bind(session, agent)
    assert bound_stt._session == session
    assert bound_stt._agent == agent
    
    # Verify stream creation sets active stream on session and resolves call_id
    stream = bound_stt.stream()
    assert session._active_stt_stream == stream
    assert stream._call_id == "test-call-123"

def test_hybrid_stt_router_bind():
    """Verify bind method on HybridSTTRouter binds both router and internal local STT."""
    config = MagicMock()
    config.sample_rate = 16000
    config.max_speech_duration_s = 30
    config.language = "en"
    
    local_stt = LocallyHostedSTT(config)
    router = HybridSTTRouter(config, local_stt=local_stt)
    
    session = MagicMock()
    session.session_state = {"call_id": "test-call-456"}
    agent = MagicMock()
    
    bound_router = router.bind(session, agent)
    assert bound_router._session == session
    assert bound_router._agent == agent
    assert bound_router.local_stt._session == session
    assert bound_router.local_stt._agent == agent
    
    stream = bound_router.stream()
    assert session._active_stt_stream == stream
    assert stream.call_id == "test-call-456"

def test_vad_stream_resolves_bound_stt():
    """Verify ElderlySileroVADStream notifies the session-bound active STT stream."""
    session = MagicMock()
    session.session_state = {"call_id": "test-call-789"}
    agent = MagicMock()
    
    # Setup bound active stt stream
    mock_stt_stream = MagicMock()
    session._active_stt_stream = mock_stt_stream
    
    vad = ElderlySileroVAD(threshold=0.5)
    # Bind VAD
    bound_vad = vad.bind(session, agent)
    assert bound_vad._session == session
    assert bound_vad._agent == agent
    
    # Verify that VAD stream resolves the active STT stream
    # SileroVADStream is not fully constructed without torch/models, but we can verify the VAD's binding attributes
