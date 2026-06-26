import pytest
from unittest.mock import MagicMock, AsyncMock
from ops.conversation_loop_doctor import analyze_timeline, run_text_injection

def test_analyze_timeline_success():
    """Verify that a complete timeline passes the analyzer."""
    events = {
        "room_joined": 100,
        "greeting_tts_started": 200,
        "greeting_audio_published": 300,
        "inbound_audio_frame_received": 400,
        "stt_stream_created": 500,
        "stt_final_transcript": 600,
        "llm_node_entered": 700,
        "agent_response_text_created": 800,
        "second_turn_tts_first_audio": 900,
        "second_turn_audio_published": 1000
    }
    # Test with event_ prefix
    prefixed_events = {f"event_{k}": v for k, v in events.items()}
    passed, broken_stage = analyze_timeline(prefixed_events)
    assert passed is True
    assert broken_stage == "none"

    # Test without prefix
    passed, broken_stage = analyze_timeline(events)
    assert passed is True
    assert broken_stage == "none"

def test_analyze_timeline_missing_stage():
    """Verify that missing stages are correctly reported by the timeline analyzer."""
    events = {
        "event_room_joined": 100,
        "event_greeting_audio_published": 300,
        # Missing inbound_audio
        "event_stt_stream_created": 500,
        "event_stt_final_transcript": 600,
        "event_llm_node_entered": 700,
        "event_agent_response_text_created": 800,
        "event_second_turn_tts_first_audio": 900,
        "event_second_turn_audio_published": 1000
    }
    passed, broken_stage = analyze_timeline(events)
    assert passed is False
    assert broken_stage == "inbound_audio"

@pytest.mark.asyncio
async def test_run_text_injection(monkeypatch):
    """Verify that run_text_injection runs without exceptions when services are mocked."""
    # We don't perform actual API calls. The function is designed to mock services.
    # Let's ensure it can execute successfully.
    monkeypatch.setenv("DANA_ALLOW_MOCK_TTS", "true")
    monkeypatch.setenv("DANA_RUNTIME_ENV", "test")
    
    # Run the injection tool path
    await run_text_injection("hello")
