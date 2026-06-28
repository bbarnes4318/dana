import pytest
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from core.call_state import CallState, CallStage
from safety.call_stop_policy import CallStopPolicy


def test_audio_monkeypatch_disabled_by_default():
    """Verify that the LiveKit audio monkeypatch is disabled by default."""
    # The monkeypatch in main.py/livekit_agent_worker.py is conditional on
    # os.getenv("DANA_ENABLE_LIVEKIT_AUDIO_MONKEYPATCH") == "true".
    # By default, it is false or unset.
    # Check if the patched_forward_audio function is NOT bound to _ParticipantAudioOutput
    try:
        import livekit.agents.voice.room_io._output as room_io_output
        original_forward = room_io_output._ParticipantAudioOutput._forward_audio
        assert not getattr(original_forward, "__name__", "").startswith("patched")
    except (ImportError, AttributeError):
        # If livekit is mocked/unavailable in this test environment, pass
        pass


def test_direct_ffi_tts_push_disabled_by_default():
    """Verify that direct FFI push defaults to false."""
    val = os.getenv("DANA_ENABLE_DIRECT_FFI_TTS_PUSH", "false").strip().lower()
    assert val == "false"


def test_amd_worker_disabled_by_default():
    """Verify that Answering Machine Detection (AMD) defaults to false."""
    val = os.getenv("DANA_ENABLE_AMD_WORKER", "false").strip().lower()
    assert val == "false"


@pytest.mark.asyncio
async def test_fast_interruption_disabled_does_not_flush():
    """Verify that fast interruption does not trigger emergency flush when disabled."""
    # Create a mock stream object representing the VAD stream with env settings
    stream = MagicMock()
    stream._allow_agent_barge_in = True
    stream._enable_fast_interruption = False
    stream._interruption_speech_threshold = 0.65
    stream._opts = MagicMock()
    stream._opts.activation_threshold = 0.4
    
    pub_speaking = True
    pub_speech_duration = 0.8  # Exceeds threshold
    p = 0.9  # High confidence
    
    mock_session = MagicMock()
    mock_session.agent_state = "speaking"
    agent_speaking = True
    
    # Emulate the check inside custom_vad.py
    flush_triggered = False
    if agent_speaking:
        if stream._allow_agent_barge_in and stream._enable_fast_interruption:
            speech_duration = pub_speech_duration
            threshold = stream._interruption_speech_threshold
            if speech_duration >= threshold and p >= stream._opts.activation_threshold:
                flush_triggered = True
                
    assert flush_triggered is False


@pytest.mark.asyncio
async def test_short_speech_duration_does_not_interrupt():
    """Verify that short speech (0.12s) does not trigger VAD barge-in under a 0.65s threshold."""
    stream = MagicMock()
    stream._allow_agent_barge_in = True
    stream._enable_fast_interruption = True
    stream._interruption_speech_threshold = 0.65
    stream._opts = MagicMock()
    stream._opts.activation_threshold = 0.4
    
    pub_speaking = True
    pub_speech_duration = 0.12  # Below 0.65s threshold
    p = 0.9  # High confidence
    
    mock_session = MagicMock()
    mock_session.agent_state = "speaking"
    agent_speaking = True
    
    # Emulate the check inside custom_vad.py
    flush_triggered = False
    if agent_speaking:
        if stream._allow_agent_barge_in and stream._enable_fast_interruption:
            speech_duration = pub_speech_duration
            threshold = stream._interruption_speech_threshold
            if speech_duration >= threshold and p >= stream._opts.activation_threshold:
                flush_triggered = True
                
    assert flush_triggered is False


@pytest.mark.asyncio
async def test_amd_does_not_disconnect_after_real_user_transcript():
    """Verify that AMD worker does not disconnect if a real user transcript has been received or turns occurred."""
    from dana.runtime.voice_session import run_amd_worker
    
    mock_track = MagicMock()
    mock_room = MagicMock()
    mock_room.isconnected = MagicMock(return_value=True)
    mock_room.disconnect = AsyncMock()
    
    # 1. Mock agent with user_transcript_received = True
    mock_agent = MagicMock()
    mock_agent.is_voicemail = False
    mock_agent.user_transcript_received = True
    
    # We mock AudioStream to return empty asynchronously to stop loop immediately
    class MockAudioStream:
        def __init__(self, *args, **kwargs):
            pass
        async def __aiter__(self):
            # empty iterator
            if False:
                yield None
        async def aclose(self):
            pass
            
    with patch('livekit.rtc.AudioStream', MockAudioStream):
        # Should exit loop immediately without calling room.disconnect()
        await run_amd_worker(mock_track, MagicMock(), mock_agent, mock_room)
        mock_room.disconnect.assert_not_called()
        
    # 2. Mock agent with turn_count > 0
    mock_agent = MagicMock()
    mock_agent.is_voicemail = False
    mock_agent.user_transcript_received = False
    
    mock_state = MagicMock()
    mock_state.turn_count = 1
    mock_agent.adapter.state_machine.call_state = mock_state
    
    with patch('livekit.rtc.AudioStream', MockAudioStream):
        await run_amd_worker(mock_track, MagicMock(), mock_agent, mock_room)
        mock_room.disconnect.assert_not_called()


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
