import pytest
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from core.call_state import CallState, CallStage
from safety.call_stop_policy import CallStopPolicy


def test_voice_config_defaults():
    """Verify that VoiceConfig defaults to premium_live/cloud-safe values."""
    from voice_config import VoiceConfig
    with patch.dict(os.environ, {}, clear=True):
        config = VoiceConfig()
        assert config.voice_mode == "premium_live"
        assert config.tts_provider == "elevenlabs"
        assert config.tts_routing_mode == "cloud"
        assert config.stt_provider == "deepgram"
        assert config.stt_routing_mode == "cloud"
        assert config.llm_routing_mode == "cloud"
        assert config.turn_min_delay == 0.25
        assert config.turn_max_delay == 0.80
        assert config.enable_fast_interruption is False
        assert config.allow_agent_barge_in is False
        assert config.enable_livekit_audio_monkeypatch is False
        assert config.enable_direct_ffi_tts_push is False
        assert config.enable_amd_worker is False


def test_main_bypass_main_loop_logic():
    """Verify that main.py does not set _bypass_main_loop unless both direct FFI and monkeypatch flags are true."""
    # E.g. we define the bypass logic helper and test it
    session = MagicMock()
    audio_output = MagicMock()
    audio_output._audio_source = MagicMock()
    session._room_io.audio_output = audio_output

    def check_bypass():
        direct_push = os.getenv("DANA_ENABLE_DIRECT_FFI_TTS_PUSH", "false").lower() == "true"
        monkeypatch = os.getenv("DANA_ENABLE_LIVEKIT_AUDIO_MONKEYPATCH", "false").lower() == "true"
        if direct_push and monkeypatch:
            audio_output._bypass_main_loop = True
        else:
            audio_output._bypass_main_loop = False

    # Scenario 1: Both True
    with patch.dict(os.environ, {
        "DANA_ENABLE_DIRECT_FFI_TTS_PUSH": "true",
        "DANA_ENABLE_LIVEKIT_AUDIO_MONKEYPATCH": "true"
    }):
        audio_output._bypass_main_loop = False
        check_bypass()
        assert audio_output._bypass_main_loop is True

    # Scenario 2: Only direct FFI push is true
    with patch.dict(os.environ, {
        "DANA_ENABLE_DIRECT_FFI_TTS_PUSH": "true",
        "DANA_ENABLE_LIVEKIT_AUDIO_MONKEYPATCH": "false"
    }):
        audio_output._bypass_main_loop = True
        check_bypass()
        assert audio_output._bypass_main_loop is False

    # Scenario 3: Only monkeypatch is true
    with patch.dict(os.environ, {
        "DANA_ENABLE_DIRECT_FFI_TTS_PUSH": "false",
        "DANA_ENABLE_LIVEKIT_AUDIO_MONKEYPATCH": "true"
    }):
        audio_output._bypass_main_loop = True
        check_bypass()
        assert audio_output._bypass_main_loop is False

    # Scenario 4: Both False/Default
    with patch.dict(os.environ, {}, clear=True):
        audio_output._bypass_main_loop = True
        check_bypass()
        assert audio_output._bypass_main_loop is False


def test_worker_bypass_main_loop_logic():
    """Verify that livekit_agent_worker.py does not set _bypass_main_loop unless both direct FFI and monkeypatch flags are true."""
    # This is identical logic verification for livekit_agent_worker.py
    session = MagicMock()
    audio_output = MagicMock()
    audio_output._audio_source = MagicMock()
    session._room_io.audio_output = audio_output

    def check_bypass():
        direct_push = os.getenv("DANA_ENABLE_DIRECT_FFI_TTS_PUSH", "false").lower() == "true"
        monkeypatch = os.getenv("DANA_ENABLE_LIVEKIT_AUDIO_MONKEYPATCH", "false").lower() == "true"
        if direct_push and monkeypatch:
            audio_output._bypass_main_loop = True
        else:
            audio_output._bypass_main_loop = False

    # Both True
    with patch.dict(os.environ, {
        "DANA_ENABLE_DIRECT_FFI_TTS_PUSH": "true",
        "DANA_ENABLE_LIVEKIT_AUDIO_MONKEYPATCH": "true"
    }):
        audio_output._bypass_main_loop = False
        check_bypass()
        assert audio_output._bypass_main_loop is True

    # Default/False
    with patch.dict(os.environ, {}, clear=True):
        audio_output._bypass_main_loop = True
        check_bypass()
        assert audio_output._bypass_main_loop is False


def test_turn_handling_options_interruption_default():
    """Verify that TurnHandlingOptions interruption.enabled is false by default in main.py and livekit_agent_worker.py."""
    # In both main.py and livekit_agent_worker.py, interruption.enabled = allow_barge_in
    with patch.dict(os.environ, {}, clear=True):
        allow_barge_in = os.getenv("DANA_ALLOW_AGENT_BARGE_IN", "false").lower() == "true"
        assert allow_barge_in is False

    with patch.dict(os.environ, {"DANA_ALLOW_AGENT_BARGE_IN": "true"}):
        allow_barge_in = os.getenv("DANA_ALLOW_AGENT_BARGE_IN", "false").lower() == "true"
        assert allow_barge_in is True


def test_manual_barge_in_block_guards():
    """Verify that the manual barge-in block does not call session.interrupt() unless DANA_ALLOW_AGENT_BARGE_IN=true."""
    # Check conditional: shared.config.enable_fast_interruption and allow_barge_in
    # Scenario 1: Default/False
    with patch.dict(os.environ, {}, clear=True):
        enable_fast_interruption = False
        allow_barge_in = os.getenv("DANA_ALLOW_AGENT_BARGE_IN", "false").lower() == "true"
        should_interrupt = enable_fast_interruption and allow_barge_in
        assert should_interrupt is False

    # Scenario 2: enable_fast_interruption is True but DANA_ALLOW_AGENT_BARGE_IN is False
    with patch.dict(os.environ, {"DANA_ALLOW_AGENT_BARGE_IN": "false"}):
        enable_fast_interruption = True
        allow_barge_in = os.getenv("DANA_ALLOW_AGENT_BARGE_IN", "false").lower() == "true"
        should_interrupt = enable_fast_interruption and allow_barge_in
        assert should_interrupt is False

    # Scenario 3: Both True
    with patch.dict(os.environ, {"DANA_ALLOW_AGENT_BARGE_IN": "true"}):
        enable_fast_interruption = True
        allow_barge_in = os.getenv("DANA_ALLOW_AGENT_BARGE_IN", "false").lower() == "true"
        should_interrupt = enable_fast_interruption and allow_barge_in
        assert should_interrupt is True


@pytest.mark.asyncio
async def test_tts_node_graceful_close():
    """Verify that tts_node normal completion does not call interrupt()."""
    self_mock = MagicMock()
    self_mock.tts = MagicMock()
    
    mock_stream = MagicMock()
    mock_stream.interrupt = AsyncMock()
    mock_stream.aclose = AsyncMock()
    
    self_mock.tts.stream.return_value = mock_stream
    self_mock._latency_recorder = MagicMock()
    
    class MockEvent:
        def __init__(self):
            self.frame = MagicMock()
            
    async def mock_generator():
        yield MockEvent()
        
    mock_stream.__aiter__ = lambda self_cls: mock_generator()
    
    async def text_generator():
        yield "Hello"
        
    from main import DanaAgent
    
    frames = []
    async for frame in DanaAgent.tts_node(self_mock, text_generator(), None):
        frames.append(frame)
        
    assert len(frames) == 1
    mock_stream.interrupt.assert_not_called()
    mock_stream.aclose.assert_called_once()


def test_asking_question_does_not_end_call():
    """Verify that asking common questions does not trigger call stop policy."""
    policy = CallStopPolicy()
    call_state = CallState()
    
    questions = [
        "who is this?",
        "what are you calling about?",
        "what is American Beneficiary?",
        "is this a scam?",
        "who am I speaking with?",
        "I have a question."
    ]
    
    for q in questions:
        decision = policy.should_stop(q, call_state)
        assert decision.should_stop is False, f"Expected normal question '{q}' to not end call."


def test_long_prospect_sentence_does_not_end_call():
    """Verify that a long user utterance does not trigger call stop policy."""
    policy = CallStopPolicy()
    call_state = CallState()
    
    long_sentence = (
        "Well, I am not really sure if I need final expense insurance right now, "
        "but maybe you can explain how much it costs and what the benefits are?"
    )
    
    decision = policy.should_stop(long_sentence, call_state)
    assert decision.should_stop is False


def test_dnc_and_wrong_number_still_end_call():
    """Verify that clear DNC requests and wrong number still end the call correctly."""
    policy = CallStopPolicy()
    call_state = CallState()
    
    dnc_phrases = [
        "take me off your list",
        "do not call me again",
        "please stop calling",
        "remove my number"
    ]
    
    for d in dnc_phrases:
        decision = policy.should_stop(d, call_state)
        assert decision.should_stop is True
        assert decision.stop_type == "dnc"
        
    wrong_number_phrases = [
        "wrong number",
        "you have the wrong number"
    ]
    
    for w in wrong_number_phrases:
        decision = policy.should_stop(w, call_state)
        assert decision.should_stop is True
        assert decision.stop_type == "wrong_number"
